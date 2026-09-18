import base64
import copy
import hashlib
from html.parser import HTMLParser
import json
import unittest

from reviewbudget.planner import analyze
from reviewbudget.reports import render_demo, render_html


def report():
    return analyze({
        "repository": "example/parser", "number": 42,
        "title": "Handle empty input", "body": "## Summary\nHandle empty input in the parser.",
        "head_sha": "a" * 40, "base_sha": "b" * 40,
        "files_complete": True, "changed_files": 1,
        "files": [{"filename": "src/parser.py", "status": "modified", "additions": 2,
                   "deletions": 1, "patch": "@@ -1 +1 @@\n-old\n+new"}],
    })


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.scripts = []
        self.styles = []
        self.csp = None
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy":
            self.csp = attrs["content"]
        if tag in {"script", "style"}:
            self.current = [attrs, ""]
            (self.scripts if tag == "script" else self.styles).append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.current = None


class ReportTests(unittest.TestCase):
    def test_standalone_report_embeds_original_planner_output(self):
        source = report()
        original = copy.deepcopy(source)
        rendered = render_html(source)
        document = Document(rendered)
        self.assertEqual(source, original)
        self.assertTrue(rendered.startswith("<!doctype html>"))
        payload = json.loads(document.scripts[0][1])
        self.assertEqual(payload, {"demo": False, "reports": [source]})
        self.assertEqual(len(document.scripts), 2)
        self.assertEqual(len(document.styles), 1)
        self.assertFalse(any("src" in attrs for _, attrs in document.tags))
        self.assertFalse(any(tag == "link" for tag, _ in document.tags))
        self.assertIn("<noscript>", rendered)

    def test_html_and_json_boundaries_escape_hostile_report_values(self):
        source = report()
        attack = '</script><script src="https://invalid.example/x.js">alert(1)</script><img src=x onerror=alert(2)>'
        source["title"] = attack
        source["repository"] = attack
        source["drivers"][0]["paths"] = [attack]
        source["drivers"][0]["message"] = attack
        source["plan"]["required"] = [attack]
        source["provenance"] = {"source": attack, "captured_at": attack}
        rendered = render_html(source)
        document = Document(rendered)
        self.assertNotIn(attack, rendered)
        self.assertIn("&lt;/script&gt;", rendered)
        self.assertEqual(len(document.scripts), 2)
        self.assertFalse(any(tag == "img" for tag, _ in document.tags))
        self.assertFalse(any(key.startswith("on") for _, attrs in document.tags for key in attrs))
        self.assertEqual(json.loads(document.scripts[0][1])["reports"][0], source)

    def test_csp_hashes_authorize_only_exact_embedded_assets(self):
        document = Document(render_html(report()))
        self.assertIn("default-src 'none'", document.csp)
        self.assertIn("connect-src 'none'", document.csp)
        self.assertIn("base-uri 'none'", document.csp)
        self.assertNotIn("unsafe-inline", document.csp)
        self.assertNotIn("unsafe-eval", document.csp)
        for _, body in document.scripts + document.styles:
            digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
            self.assertIn(f"'sha256-{digest}'", document.csp)

    def test_demo_contains_distinct_unmodified_scenarios(self):
        one, two = report(), report()
        two["number"] = 43
        two["title"] = "Different scenario"
        document = Document(render_demo([one, two]))
        self.assertEqual(json.loads(document.scripts[0][1]), {"demo": True, "reports": [one, two]})

    def test_nonfinite_numbers_and_invalid_report_roots_are_rejected(self):
        for source in [{}, {"schema_version": 2}, {**report(), "tier": True}, {**report(), "tier": 8}]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                render_html(source)
        for value in [float("nan"), float("inf"), -float("inf")]:
            source = report()
            source["risk"]["score"] = value
            with self.assertRaises(ValueError):
                render_html(source)
        for reports in [[], [report()] * 31]:
            with self.assertRaises(ValueError):
                render_demo(reports)

    def test_data_uses_no_html_insertion_or_external_requests(self):
        script = Document(render_html(report())).scripts[1][1]
        # The renderer uses only text nodes for report-controlled content. Keep this
        # invariant explicit alongside parser-boundary tests and browser verification.
        for operation in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "fetch(", "XMLHttpRequest", "localStorage", "sessionStorage"]:
            self.assertNotIn(operation, script)
        self.assertIn("file.size > maxImportBytes", script)
        self.assertIn("validateReport(report)", script)
        self.assertIn("textContent", script)


if __name__ == "__main__":
    unittest.main()
