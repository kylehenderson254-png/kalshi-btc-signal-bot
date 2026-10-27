QUEEN’S EDGE — Phase 2 compact market overview

Upload all six files into queens-edge-preview branch ROOT. Do not merge to main.

Phase 2 adds four-across Kalshi market overview, purple neon cards, live browser-side timers, compact Early/Mid/Late status, and collapsible legacy detailed cards. The legacy trackers, Supabase tables, pressure radar and settlement logic remain unchanged.

Important limitations:
- Underlying live spot/settlement-reference prices are NOT wired yet, so the UI explicitly says Feed pending / Not verified. No fabricated differences.
- Mid call is NOT implemented and will be labeled as such.
- Trade readiness is WAIT/not evaluated, not a recommendation.
- YES/NO quotes still update at the selected polling interval; browser timers tick each second.
- Existing Early and Locked Call storage can write if Supabase secrets are configured; keep preview secrets empty until intentionally testing with a separate database or safe read-only strategy.
- Do not copy secrets into GitHub.
- Desktop layout is four-across; Streamlit may stack columns on mobile.

To deploy: GitHub queens-edge-preview branch > Add file > Upload files > select the six extracted files > Commit directly to queens-edge-preview. Streamlit preview auto-redeploys.
