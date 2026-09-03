"""Build the deployable public site into dist/ and audit it.

Usage:
    .venv/Scripts/python.exe scripts/build_public_site.py
    .venv/Scripts/python.exe scripts/build_public_site.py --preview surfeit:draft

The default build renders ONLY public-safe content (published issue snapshots,
approved matchup previews, public team names, verbatim historical archive) and
then audits its own output for private material; a non-empty audit fails the
build.

--preview <league>:<issue_key> additionally renders one UNPUBLISHED issue with
a commissioner-preview banner, into dist-preview/ instead of dist/. Never
deploy dist-preview/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leaguepage.config import DIST_DIR, REPO_ROOT
from leaguepage.site_build import audit_output, build_site
from leaguepage.storage import Storage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", help="league:issue_key to include as an "
                                          "unpublished commissioner preview "
                                          "(output goes to dist-preview/)")
    args = parser.parse_args()

    preview_issues = None
    out_dir = DIST_DIR
    if args.preview:
        league_slug, _, issue_key = args.preview.partition(":")
        preview_issues = {league_slug: issue_key}
        out_dir = REPO_ROOT / "dist-preview"

    with Storage() as storage:
        result = build_site(storage, out_dir=out_dir, preview_issues=preview_issues)

    print(f"Built {len(result['pages'])} pages into {result['out_dir']}")
    for w in result["warnings"]:
        print(f"  warning: {w}")

    violations = audit_output(result["out_dir"],
                              public_names=result.get("public_names"))
    if args.preview:
        # rough markers are expected inside the flagged preview issue; still
        # report them so nothing is silent, but do not fail the preview build
        print(f"privacy audit (preview build): {len(violations)} finding(s)")
        for v in violations[:20]:
            print(f"  {v}")
        print("dist-preview/ is for local review only. NEVER deploy it.")
        return 0
    if violations:
        print(f"PRIVACY AUDIT FAILED — {len(violations)} finding(s):")
        for v in violations[:40]:
            print(f"  {v}")
        return 1
    print("privacy audit: clean. dist/ is deployable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
