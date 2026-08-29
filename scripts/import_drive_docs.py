"""One-time bulk import of the historical newsletter Google Docs.

Downloads each doc's plain-text export (the docs are link-shared, so no auth),
writes archive/<league>/<file>.md with frontmatter, then run
scripts/import_archive.py to index them.

Seasons in the frontmatter are NFL season start years. Jonathan's title
convention changed over time ("2024 Disco Week 1" was written in Sept 2023),
so seasons here were derived from each doc's creation date, not its title.
Titles are kept verbatim.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
ARCHIVE = REPO / "archive"

# (file_id, league, season, week, title, outfile)
DOCS = [
    # --- Root: 2019 Daddy issues (Big Daddy AF) ---
    ("1R7GaxAIOb6jDJy1YoYfqBRIY42gHP_0owpfzALlCBZw", "daddy", "2019", 12, "Daddy 12", "daddy/2019-week-12.md"),
    ("1fT0YUHGD-eYc5Vfxj7B7h4_BTX8bez8tcaNvmdgWnEY", "daddy", "2019", 13, "Daddy 13", "daddy/2019-week-13.md"),
    ("1hR0bcitZ4npWFO3IJc-5TajdVokZNTEU5hF7chr8BMI", "daddy", "2019", 14, "Daddy 14", "daddy/2019-week-14.md"),
    ("1MkiOMevZIBZakMRb_x8SbvABcB84tGIQSjeQ0cmqxss", "daddy", "2019", 15, "Daddy 15", "daddy/2019-week-15.md"),
    ("1At1WrXOztydDwHiyhF3Tbg8hVz9DU7JBMJJ821bGfDg", "daddy", "2019", 16, "Daddy 16", "daddy/2019-week-16.md"),
    # Daddy 17 (1vXNkOIpNM2m...) is not link-shared; imported 2026-08-29 via the
    # authenticated Drive connector instead. Same for the other ids noted below.
    # --- Root: 2019 Disco issues ---
    ("1TD-utm5d_pOrMqZJsPp-7HOGAuPvYuX7r1ONi_j-h18", "disco", "2019", 12, "Disco 12", "disco/2019-week-12.md"),
    ("1bEqWV44eUEbAXxyVb99a2zofOJ6_O4U521Nm1bV8rwM", "disco", "2019", 13, "Disco 13", "disco/2019-week-13.md"),
    ("1Qz-o2m6vig3QjqvbhJYEP9hs3g77o5YMuv5P1V-Bu9g", "disco", "2019", 14, "Disco 14", "disco/2019-week-14.md"),
    ("1Z4wE0Q5Lr-uD8jqBoKyBXTl1oSUyY0feWobnthsBSLs", "disco", "2019", 15, "Disco 15", "disco/2019-week-15.md"),
    ("1LefhSciaBT9PFeh_7FAaPR9V1HLlu3JQyzZL6nvqBiQ", "disco", "2019", 16, "Disco 16", "disco/2019-week-16.md"),
    # Disco 17 (12uQ8S1yZ_...): imported via Drive connector, see note above.
    # --- Root: specials ---
    ("19sVpCoBNpU2nQE3K3XScmvSukz2T5BMVvns4r41T82U", "daddy", None, None, "Daddy Stats", "daddy/daddy-stats.md"),
    ("1ce2RfX9PQqhnWaCbLEoeonICJCsS7MgegnKcEij__3o", "daddy", "2019", None, "Big Daddy AF Side Bets", "daddy/2019-side-bets.md"),
    ("1fE9YTgHj8LBLuod5W7_Lna0d5oFyxiToouN-z_dn_EI", "daddy", "2019", None, "League Analysis: Time the First", "daddy/2019-league-analysis.md"),
    # The two "Newsletter" docs at the Drive root are untouched Google lorem-ipsum
    # templates — verified 2026-08-29, deliberately not imported.
    # --- Daddy folder ---
    ("1_jdSB6z0CzTesm1qRjLMRjFZksPVm8bHQG3vIKAeXG4", "daddy", "2020", 5, "2020 Daddy Week 5 Preview", "daddy/2020-week-05.md"),
    ("11lVYXJ2-LHHzhGBzfZdGa_mnZbPV2VXL0ZmothDk-sE", "daddy", "2021", 1, "2021 Daddy Week 1 Preview and Trade Review", "daddy/2021-week-01.md"),
    ("1XC9755685qTg8Ekt2VTtQ4uoMdCOFMYHfX1E5gCt7-M", "daddy", "2021", 2, "2021 Daddy Week 2 Preview", "daddy/2021-week-02.md"),
    ("13VC_SCqQJagpkHU1mArME825rNKQj9sTEJ6sX6U9uhE", "daddy", "2021", 3, "2021 Daddy Week 3 Preview", "daddy/2021-week-03.md"),
    # --- Disco folder: 2020 season ---
    ("1HoAI_LNWkfIHd-QCX7M6705pANUwoprfBre34XVSdFM", "disco", "2020", None, "Disco Post-Draft", "disco/2020-post-draft.md"),
    ("1zRpnl1KumZZ80KjwSPMPbMnhDbCBvSAgOg7XcUHHvc0", "disco", "2020", 5, "Disco Week 5 Preview", "disco/2020-week-05.md"),
    # Disco Week 6 Preview (1Fb-KYoCC1...): imported via Drive connector, see note above.
    # --- Disco folder: 2021 season ---
    ("1EYqmkSCo1ZhsCGzWWUnLgXWl9sATT3SPBJzvtv8KNsQ", "disco", "2021", 1, "2021 Disco Week 1 Preview/Draft Review", "disco/2021-week-01.md"),
    ("1bK1aViUiVtEjI1hd7Y7D6FschQlBmVu2HYCVGmzdBio", "disco", "2021", 2, "2021 Disco Week 2 Preview", "disco/2021-week-02.md"),
    ("1xHg-QtW7AX6gu514bNiLMSeGB_MynZvMmc2rqdIAvHc", "disco", "2021", 3, "2021 Disco Week 3", "disco/2021-week-03.md"),
    ("1ZNSCL3fCBvj0Q3k7AeU0ySpCNjW90L_gMGjrbN55H04", "disco", "2021", 4, "2021 Disco Week 4", "disco/2021-week-04.md"),
    ("1HtjhmqSj3-j3ojT5sKtaZpOXQeBjxekEGLQ-wMZFrDI", "disco", "2021", 5, "2021 Disco Week 5", "disco/2021-week-05.md"),
    ("1H3AcLTFf5j7D5X6yA1lcohBWqngx9gceVw8lLVgZOBU", "disco", "2021", 6, "2021 Disco Week 6", "disco/2021-week-06.md"),
    ("1we9uxl5bgvfy3JBmwM_qfBnJzYC8jvizwgKiHCwsKbg", "disco", "2021", 7, "2021 Disco Week 7", "disco/2021-week-07.md"),
    ("1vCyoeTU4FmoTjmwrsVKg19O5_erbzAZdBDQkEEpVrLY", "disco", "2021", 8, "2021 Disco Week 8", "disco/2021-week-08.md"),
    ("1HWvEgKX_QweLQ5HXfFHj8SQn46Yhxki5tF5UZUpnKvI", "disco", "2021", 9, "2021 Disco Week 9", "disco/2021-week-09.md"),
    ("19-DFP6KUU-wb-Lf-ZhEUsdSuhUGRku9de5PfKz3K6V4", "disco", "2021", 10, "2021 Disco Week 10", "disco/2021-week-10.md"),
    ("1nQtajLTJx-hec8_08dy_K30oD9N3m5dj2koedJO-Rkk", "disco", "2021", 11, "2021 Disco Week 11", "disco/2021-week-11.md"),
    ("1M5lq76K8T_VUUV6nEtTn5JcHYzd_1QuuGr7sV3taApE", "disco", "2021", 12, "2021 Disco Week 12", "disco/2021-week-12.md"),
    ("1G7NaZ2sZEhTaH6b2UD1u_O8nsB7NQ8SYD5cXOdQL2_E", "disco", "2021", 13, "2021 Disco Week 13", "disco/2021-week-13.md"),
    ("1ufDPrugdCC3lbCZ2HqxezjO03bau6k4o1gAFnq-Pz34", "disco", "2021", 14, "2021 Disco Week 14", "disco/2021-week-14.md"),
    ("1MdvR8THpEfx7zbEhN5gYnybEiCuwtJAowAVJwrbblEg", "disco", "2021", 15, "2021 Disco Week 15", "disco/2021-week-15.md"),
    ("1vBdcdByawsLX2GhpqU7hO1lAYnnFr443Q8ripPeWv1M", "disco", "2021", 16, "2021 Disco Week 16", "disco/2021-week-16.md"),
    ("1Igatu6pL5p9PziUEG3B1E2PiaZZHFLl7E4V-G3PF0OA", "disco", "2021", 17, "2021 Disco Week 17", "disco/2021-week-17.md"),
    # --- Disco folder: 2022 season (titled "2023 Disco ...") ---
    ("1XvWtyqdMM035FtDV5DT6tHD3llWqpwNZh9AXVfrhCcU", "disco", "2022", 1, "2023 Disco Week 1", "disco/2022-week-01.md"),
    ("1ucXvKzk_n-XjLL7CyXeaAA4UW1c7CMTEYHl23ppcA5M", "disco", "2022", 3, "2023 Disco Week 3", "disco/2022-week-03.md"),
    ("18mRRF0P5M8qlxuvbXaw_gQtl5_ANxdiYjDgq_Gkvo4Y", "disco", "2022", 4, "2023 Disco Week 4", "disco/2022-week-04.md"),
    ("1lrNZ-LbFJr22WUIPKZeoarrbhLcCaB4MzHj-OC9s3o4", "disco", "2022", 5, "2023 Disco Week 5", "disco/2022-week-05.md"),
    ("1sCtjkM29wAloCTGMA7t6fpYZftMlf2NBiJf18EtdOL8", "disco", "2022", 6, "2023 Disco Week 6", "disco/2022-week-06.md"),
    ("1RaSLLB4Zz9zy_zB26hH6L7aYDUDR5B-XgDFEb4psqV8", "disco", "2022", 7, "2023 Disco Week 7", "disco/2022-week-07.md"),
    ("1EVXobjDeG5YUbIkSnB9EzSTQAKApnKajMAdCkSspuFE", "disco", "2022", 8, "2023 Disco Week 8", "disco/2022-week-08.md"),
    ("1nElA6YkQzMwddKOgdhNJkNxxMklKE1Zea1OgfF2gE3U", "disco", "2022", 9, "2023 Disco Week 9", "disco/2022-week-09.md"),
    # --- Disco folder: 2023 season (titled "2024 Disco ...") ---
    ("1krqHDeQRAnZeo4XaNzXLKLCQ4BepvSAWpKRmFoe6Bqc", "disco", "2023", 1, "2024 Disco Week 1", "disco/2023-week-01.md"),
    ("1g6w70CriaD7h7VB0dG54HKLVpD1sJbEFg_kDiMVua9k", "disco", "2023", 2, "2024 Disco Week 2", "disco/2023-week-02.md"),
    ("1pPDTzjCsiFATGUt3bqvhKxJDgdzlmhSx6Vp2c3Ck0h4", "disco", "2023", 4, "2024 Disco Week 4", "disco/2023-week-04.md"),
    ("1GiZVAcLz37iv90Do3QIqnxLrqpKSVHOda7nNwe5I4GQ", "disco", "2023", 5, "2024 Disco Week 5", "disco/2023-week-05.md"),
    # 2024 Disco Week 6 (1AgIo9vhHg...): imported via Drive connector, see note above.
    ("1dtdtUw5KmEaXMzcTj_HNHysgn25ceoHruR0Q0lywlSM", "disco", "2023", 16, "2024 Disco Week 16", "disco/2023-week-16.md"),
    # --- Disco folder: 2025 season ---
    ("1acBhGcBMd-dUfPg0Lh7cesnb64jgmRl6qCr5YfROMXY", "disco", "2025", 5, "2025-26 Disco Week 5", "disco/2025-week-05.md"),
    ("1wEhCAksXMnCL5utKQah4ryS-jn9o9cqVQZ2oMBKkv9c", "disco", "2025", 7, "2025-26 Disco Week 7", "disco/2025-week-07.md"),
]

MIN_CHARS = 300  # skip empty shells / abandoned templates


def main() -> int:
    failures = 0
    for file_id, league, season, week, title, outfile in DOCS:
        url = f"https://docs.google.com/document/d/{file_id}/export?format=txt"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"FAIL  {title}: {exc}")
            failures += 1
            continue
        resp.encoding = "utf-8"
        text = resp.text.lstrip("﻿").replace("\r\n", "\n").strip()
        if len(text) < MIN_CHARS:
            print(f"SKIP  {title}: only {len(text)} chars")
            continue
        fm = ["---", f"league: {league}"]
        if season:
            fm.append(f"season: {season}")
        if week is not None:
            fm.append(f"week: {week}")
        fm += [f"title: {title}", f"source: google-doc {file_id}", "---", ""]
        out = ARCHIVE / outfile
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(fm) + text + "\n", encoding="utf-8")
        print(f"wrote {outfile} ({len(text)} chars)")
        time.sleep(0.5)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
