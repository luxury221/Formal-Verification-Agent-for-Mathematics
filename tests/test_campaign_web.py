"""Loopback campaign contracts; fixture proofs remain explicitly NONEXECUTABLE."""

from copy import deepcopy
import shutil
import unittest
from unittest.mock import patch

from test_http_api import request, running_server
from test_research_campaign import ContractTests as Fixtures, ROOT, reviewed
from mra.campaign_checks import CampaignChecks
from test_research_campaign import synthetic


class HttpTests(unittest.TestCase):
    def setUp(self):
        Fixtures.setUp(self)
        shutil.copytree(ROOT / "mra/web", self.root / "mra/web", dirs_exist_ok=True)

    def test_full_http_review_submit_report_export_cancel_and_withdraw(self):
        with running_server(self.root) as server:
            server.campaigns = self.store
            base = "/v1/research-campaigns"
            status, catalog, _ = request(
                server, "GET", base + "/catalog?flow_id=" + self.flow["flow_id"]
            )
            self.assertEqual(status, 200)
            self.assertEqual(catalog["flow"]["flow_id"], self.flow["flow_id"])
            status, plan, _ = request(server, "POST", base + "/preview", self.value)
            self.assertEqual((status, plan["evidence_level"]), (200, "E0"))
            body = reviewed(self.store, self.value)
            status, row, _ = request(server, "POST", base + "/runs", body)
            self.assertEqual(status, 201)
            key = row["campaign_id"]
            self.assertEqual(request(server, "POST", base + "/runs", body)[1]["campaign_id"], key)
            with patch.object(CampaignChecks, "execute", synthetic):
                self.store.work_once(key)
            detail = request(server, "GET", base + "/runs/" + key)[1]
            self.assertEqual(detail["math_verdict"], "PROVED")
            self.assertEqual(detail, request(server, "GET", base + "/runs/" + key + "/export")[1])
            self.assertEqual(
                len(
                    request(server, "GET", base + "/runs?flow_id=" + self.flow["flow_id"])[1][
                        "runs"
                    ]
                ),
                1,
            )
            withdrawal = {
                "request_id": "web-withdraw",
                "node_id": "middle",
                "reviewer": "NONEXECUTABLE",
                "reason": "web contract",
                "confirmed": True,
            }
            self.assertEqual(
                request(server, "POST", base + "/runs/" + key + "/withdraw", withdrawal)[1][
                    "math_verdict"
                ],
                "UNKNOWN",
            )
            second = request(server, "POST", base + "/runs", reviewed(self.store, self.value))[1]
            self.assertEqual(
                request(server, "POST", base + "/runs/" + second["campaign_id"] + "/cancel", {})[1][
                    "state"
                ],
                "CANCELLED",
            )

    def test_http_guards_queries_duplicate_json_stale_review_and_assets(self):
        with running_server(self.root) as server:
            server.campaigns = self.store
            base = "/v1/research-campaigns"
            for route in (
                "/catalog",
                "/catalog?flow_id=",
                "/runs?unknown=1",
                "/runs?flow_id=x&flow_id=y",
            ):
                self.assertEqual(request(server, "GET", base + route)[0], 400, route)
            for route in ("/preview?extra=1", "/runs?extra=1"):
                self.assertEqual(request(server, "POST", base + route, self.value)[0], 400)
            self.assertEqual(
                request(
                    server,
                    "POST",
                    base + "/preview",
                    self.value,
                    headers={"Origin": "https://invalid.example"},
                )[0],
                403,
            )
            self.assertEqual(
                request(
                    server, "POST", base + "/preview", self.value, headers={"X-MRA-CSRF": "wrong"}
                )[0],
                403,
            )
            self.assertEqual(
                request(server, "POST", base + "/preview", raw=b'{"flow_id":"a","flow_id":"b"}')[0],
                400,
            )
            body = reviewed(self.store, self.value)
            body["plan_fingerprint"] = "stale"
            self.assertEqual(request(server, "POST", base + "/runs", body)[0], 400)
            bad = deepcopy(self.value)
            bad["actions"]["middle"]["request"] = []
            self.assertEqual(request(server, "POST", base + "/preview", bad)[0], 400)
            for route in ("/campaigns", "/campaigns.js", "/campaigns.css"):
                self.assertEqual(request(server, "GET", route)[0], 200)


if __name__ == "__main__":
    unittest.main()
