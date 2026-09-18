import base64
import copy
import hashlib
from html.parser import HTMLParser
import json
import unittest

from reviewbudget.planner import analyze
from reviewbudget.reports import render_collection, render_demo, render_html, render_queue


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
        self.visible = []
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
        else:
            self.visible.append(data)

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

    def test_real_collection_preserves_provenance_without_demo_mode(self):
        source = report()
        source["provenance"] = {"source": "github", "synthetic": False}
        document = Document(render_collection([source]))
        payload = json.loads(document.scripts[0][1])
        self.assertFalse(payload["demo"])
        self.assertTrue(payload["collection"])
        self.assertNotIn("queue", payload)
        self.assertEqual(payload["reports"], [source])
        self.assertNotIn("Synthetic examples", "".join(document.visible))

    def test_queue_preserves_global_accounting_separate_from_report_shares(self):
        source = report()
        source["budget"] = {"scope": "queue_share", "limit_minutes": None,
                            "queue_limit_minutes": 20, "estimated_minutes": None}
        queue = {"schema_version": 1, "mode": "advisory", "reports": [source],
                 "budget": {"limit_minutes": 20, "selected_known_minutes": 35,
                            "mandatory_known_minutes": 35, "shortfall_minutes": 15,
                            "estimated_minutes": None, "within_budget": False,
                            "unpriced_checks": ["example/parser#42/security"],
                            "unmapped_capabilities": ["example/parser#42/human_review"]},
                 "recommendations": ["Keep required checks selected."]}
        original = copy.deepcopy(queue)
        document = Document(render_queue(queue))
        payload = json.loads(document.scripts[0][1])
        self.assertEqual(queue, original)
        self.assertFalse(payload["demo"])
        self.assertTrue(payload["collection"])
        self.assertEqual(payload["reports"], queue["reports"])
        self.assertEqual(payload["queue"], {key: value for key, value in queue.items() if key != "reports"})
        self.assertEqual(payload["queue"]["budget"]["limit_minutes"], 20)
        self.assertIsNone(payload["reports"][0]["budget"]["limit_minutes"])

    def test_local_diff_fallback_never_claims_placeholder_pull_request(self):
        for nested in [False, True]:
            source = report()
            source["number"] = 1
            source.pop("title", None)
            source["source"] = "local_git"
            if nested:
                source["provenance"] = {"source": "local_git", "synthetic": False}
            document = Document(render_html(source))
            visible = "".join(document.visible)
            self.assertIn("local diff", visible)
            self.assertIn("base bbbbbbbb / head aaaaaaaa", visible)
            self.assertNotIn("PR #1", visible)
            self.assertNotIn("example/parser #1", visible)

    def test_invalid_queue_shapes_fail_before_rendering(self):
        for source in [None, {}, {"schema_version": 2, "budget": {}},
                       {"schema_version": 1, "budget": [], "reports": [report()]},
                       {"schema_version": 1, "budget": {}, "reports": []}]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                render_queue(source)

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
        self.assertIn("source.size > maxImportBytes", script)
        self.assertIn("new TextEncoder().encode(text).byteLength > maxImportBytes", script)
        self.assertIn("validateReport(report)", script)
        self.assertIn("textContent", script)

    def test_paste_import_has_accessible_input_and_explicit_action(self):
        document = Document(render_html(report()))
        controls = {attrs["id"]: (tag, attrs) for tag, attrs in document.tags if "id" in attrs}
        self.assertEqual(controls["paste-report"][0], "details")
        self.assertEqual(controls["report-json"][0], "textarea")
        self.assertIn("import-status", controls["report-json"][1]["aria-describedby"])
        self.assertTrue(any(tag == "label" and attrs.get("for") == "report-json" for tag, attrs in document.tags))
        self.assertEqual(controls["view-pasted-report"][1]["type"], "button")
        self.assertEqual(controls["import-status"][1]["aria-live"], "polite")
        self.assertEqual(controls["report-file"][1]["type"], "file")


if __name__ == "__main__":
    unittest.main()
