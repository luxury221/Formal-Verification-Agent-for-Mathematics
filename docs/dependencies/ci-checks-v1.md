# CI report retention dependency

Adoption checked on 2026-11-02, before use in the updated workflow.

| Component | Pinned version | License | Purpose and limits |
| --- | --- | --- | --- |
| `actions/upload-artifact` | v4.6.2, commit `ea165f8d65b6e75b540449e92b4886f43607fa02` | MIT | Upload only the explicit JSON test report from ephemeral GitHub runners, retained for 14 days. This is CI infrastructure; it grants no mathematical evidence. GitHub storage, retention and platform terms apply. |

The GitHub tag API was checked against the pinned commit. The pinned
[LICENSE](https://github.com/actions/upload-artifact/blob/ea165f8d65b6e75b540449e92b4886f43607fa02/LICENSE)
blob is `a67dca8b4f65d6bd351f6b1e333ce2cd84d843a5`; the upstream license API reports
MIT. This is a version/license record, not a complete dependency or security audit
of GitHub's infrastructure. No action implementation is copied into product code.

The existing pinned `actions/checkout` remains unchanged. The test runner adds no
Python package, SDK, model, theorem library or product runtime dependency.
