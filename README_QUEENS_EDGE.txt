QUEEN’S EDGE — PHASE 1 PREVIEW

This is a FIRST-PASS upgrade, not yet the final screenshot-matching UI.
Includes branded bull/bear banner, dark-purple theme, independent live per-market countdowns, and removes 3 large redundant charts.
Keeps existing Kalshi quote polling, prediction rules, Supabase Early and Locked Call tables, and pressure radar.
No new live underlying-price feed, independently calibrated confidence, or Mid Call tracking yet.

TEST SAFELY:
1. Create a separate GitHub branch named queens-edge-preview (do not edit main).
2. Upload the contents of this ZIP to the ROOT of that branch, replacing files of the same names there.
3. Deploy the preview branch as a SEPARATE Streamlit app.
4. Do NOT configure the preview with your production Supabase secrets yet: the original app and preview should not both run the tracker on production data without reviewing side effects. Without secrets, calls/scoreboards cannot load; this is expected in visual preview.
5. Review visuals and timer, then continue next phase.

Do not upload your secrets.toml or service role key to GitHub or ChatGPT.
