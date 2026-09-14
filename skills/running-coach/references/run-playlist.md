# Run Playlist — Workflow

Build Karan a playlist sized to the day's run, matched to his taste and recent
listening, with a build-and-strong-finish arc.

## Workflow

1. **Get the planned distance.** Ask if he hasn't said. Genre is optional and
   defaults to Hindi indie / pop.
2. **Refresh what the prompt needs.** `python3 scripts/cache.py status`.
   - `Strava:get_activity_performance` on recent runs →
     `put performance --activity-id <id>` (this is what sizes the playlist)
   - `Spotify:search` for "my recently played songs" → `put spotify_recent`
3. **Build the prompt.** `python3 scripts/playlist/build_playlist.py <km>`,
   using the same distance that was planned.
   It projects the finish time through the same pace model as planning, sizes
   the playlist to it, seeds from the cached artists, and prints the numbered
   Spotify call sequence.
4. **Execute that sequence** with the Spotify tools, in order:
   - `Spotify:remove_from_library` on the pinned playlist id, so the library
     keeps exactly one run playlist
   - `Spotify:generate_playlist` with the printed prompt
   - `Spotify:save_to_library` on the new playlist URI
5. **Record the new playlist** so the next run can delete it:

   ```bash
   python3 scripts/cache.py put playlist_state --set playlist_id=<new id> \
       --set "name=Today Run's Playlist"
   ```

6. **Give Karan the link**, and mention he'll need to rename the playlist in
   Spotify if he wants the exact pinned title.

## Scripts

**`build_playlist.py`** — sizing and prompt assembly.

```bash
python3 scripts/playlist/build_playlist.py 8
python3 scripts/playlist/build_playlist.py 10 --genre "Hindi indie" --json
python3 scripts/playlist/build_playlist.py 5 --minutes 30   # skip the projection
python3 scripts/playlist/build_playlist.py 5 --band hard
```

`--json` returns `{duration, band, seed_artists, pinned_playlist, prompt,
mcp_steps}`. Use `--minutes` only when Karan gives a duration directly;
otherwise let the pace model size it.

## Pinned playlist

The current playlist id lives in the cache, not in this file:

```bash
python3 scripts/cache.py show playlist_state
```

Step 5 above is what keeps it current. If it is unset, `build_playlist.py` says
so and skips the delete step rather than failing.

## Known limitation

The Spotify tools can generate, save and remove playlists, but cannot rename a
playlist or edit the tracks inside an existing one. So every run produces a new
AI playlist with a new id. Deleting the previous one first is what keeps the
library from filling up with near-duplicates.
