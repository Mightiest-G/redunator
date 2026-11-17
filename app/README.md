# Redunator — Flutter app (Phase 4)

Chicago red-light-camera ticket-risk predictor. Map, hourly risk
estimates, route risk, and proximity alerts, backed by the Redunator
FastAPI backend (`../backend/`). See `API_REFERENCE.md` for the full
backend API, environment, and proxy/build recipe.

## What it does

- **Map (home)** — color-coded camera markers from `GET /intersections`
  + `POST /predict_risk/batch` (chunked at 100 items, the backend limit).
  Marker colours (PDF thresholds, on the backend `risk_score` 0–1):
  **Green <40%, Yellow 40–70%, Red >70%**. Tap a marker for the detail
  sheet: daily prediction, the 24-hour curve with hourly bands, the
  reasons from `/predict_risk`, recent daily trends, and **Report Spike**.
- **Query** — pick an intersection and a date/hour; shows the risk for
  that hour, labelled as the hourly estimate for that hour.
- **Route** — pick origin/destination; the route line is coloured by
  the highest-risk camera within 300 m of each segment (same colours),
  with the nearby cameras listed. Routing: **OSRM** (free, no key —
  see below), Google Directions as fallback.
- **Settings** — alert distance (default 300 m), risk threshold
  (default 75%), backend base URL, units, Maps-key status, backend
  health check, alert-sound test, feedback.
- **Offline mode (SDD FR12)** — the last fetched intersections +
  predictions are cached on device; when the backend is unreachable
  the cached map shows with a clear "Offline — showing cached data
  (X old)" banner. Cached data is never presented as live.
- **Proximity alerts** — foreground location watcher (geolocator) +
  audio alert (audioplayers) within the configured distance of a
  camera above the configured risk threshold, 5-minute cooldown per
  camera. Background operation is **not** implemented: see below.

## Honest labelling (do not weaken)

- **Hourly values are ESTIMATES** — "Hourly values are estimates from
  proxies — not observed hourly violations." The daily total is the
  modelled quantity; the hourly shape comes from crash/congestion
  proxies renormalised to the daily total.
- **Bands** — Daily: LOW <4, MEDIUM 4–<10, HIGH ≥10 predicted
  violations/day. Hourly (Phase 1 fixed): LOW <0.1, MEDIUM 0.1–<0.5,
  HIGH ≥0.5 predicted/hour.
- **Risk score** — the app uses the backend's returned `risk_score`
  (`clip(pred/35, 0, 1)`) verbatim. It does **not** invent a new hourly
  score scale (an open decision is pending on that).
- **Live adjustment does NOT ship** — `/live/risk` always returns
  `adjustment_applied: false, factor: 1.0`; the app applies no live
  adjustment of its own.
- **Confidence is a heuristic**, not a calibrated probability.

## Google Maps API key — REQUIRED for the map

The key lives in **one place: `lib/config.dart`**
(`const String googleMapsApiKey = '...';`). The same value is read by
`android/app/build.gradle.kts` and injected into
`AndroidManifest.xml` as `com.google.android.geo.API_KEY`. There is no
`--dart-define` or `.env` mechanism — `lib/config.dart` is the only one.

1. Go to [console.cloud.google.com](https://console.cloud.google.com),
   open (or create) a project.
2. APIs & Services → Library → enable **Maps SDK for Android**.
   (Also enable **Directions API** if you want the Google Directions
   fallback for routing; OSRM routing works without it.)
3. APIs & Services → Credentials → Create Credentials → API key.
4. Restrict the key: Application restrictions → Android apps, package
   `com.redunator.redunator`; API restrictions → only the APIs above.
5. Paste the key into `lib/config.dart` and rebuild.
6. **Do not commit a real key.** `lib/config.dart` ships with an empty
   key on purpose; keep your key out of version control.

**When the key is missing/empty the app does NOT crash and does NOT
show a blank map**: the Map and Route tabs show a full-screen
explanation with these steps, and the Query + Settings tabs keep
working. The app bar also shows a "No Maps key" chip.

## Backend

Default base URL: `http://10.0.2.2:8000` — the Android emulator's
route to a backend running on the host machine. Change it in Settings
(for a physical device use the host's LAN IP, e.g.
`http://192.168.1.10:8000`, or a deployed URL).

Start the backend from `~/workspace/redunator/`:

```bash
~/workspace/redunator-venv/bin/python -m backend.app
```

## Routing source

**Primary: OSRM** — `https://router.project-osrm.org`, free, no key,
no account. Chosen because it needs nothing from Gabriel and works
out of the box. It is a public demo server with modest rate limits.
**Fallback: Google Maps Directions**, using the *same* Maps key as the
map (only when OSRM fails and a key is configured; requires the
Directions API enabled on that key). If both fail, the Route screen
shows the exact error and the OSRM URL to check in a browser.

## Proximity alerts — platform limits

- **Foreground: fully supported.** The watcher runs while the app is
  open (any tab).
- **Background: NOT supported in this build.** Android allows
  background location only with the user-granted "Allow all the time"
  permission plus a foreground service; iOS only with "Always"
  permission plus the location background mode, and iOS may still
  throttle it. This app runs no background service, so alerts pause
  when the app is backgrounded or killed, and resume when reopened.
  The Settings screen states this in plain language. Adding a
  foreground service is the known follow-up if Gabriel wants true
  background alerts.

## Build (this VM — proxy recipe from API_REFERENCE.md)

```bash
source ~/workspace/.tmp/build_env.sh   # IPv6-literal proxy, TLS 1.2, JDK 17
cd ~/workspace/redunator/app
flutter pub get
flutter analyze
flutter test
flutter build apk --release
```

Release APK (Worker 3, 2026-11-16 ~15:04 CST):
`build/app/outputs/flutter-apk/app-release.apk` — **53,659,706 bytes**
(53.7 MB), sha256
`3780a557ee9ff6857fd05d9505c94b8f21963ea76bf229b1c084c6590da1ab33`.
aapt: package `com.redunator.redunator`, versionCode 1, versionName
1.0.0, compileSdk 36, targetSdk 36, minSdk 24, launchable
`com.redunator.redunator.MainActivity`. (Debug APK from Worker 2:
162,728,020 bytes — release is ~1/3 the size.)

Notes: `/tmp` is a 512 MB tmpfs — `TMPDIR=~/workspace/.tmp` is set by
`build_env.sh`. Run `flutter pub get` once before building (a
`--no-pub` build fails on missing `package_config.json`). Kill stale
Gradle daemons after JDK/env changes (kill by PID — `pkill -f
GradleDaemon` matches your own shell's command line and kills it;
use `~/workspace/.tmp/kill_daemons.sh` if you must — note that script
itself uses `pkill -f`, so run it from a shell whose command line does
not contain the pattern, or kill the daemon PID directly). The build takes
~17 min on this VM with `org.gradle.workers.max=1`.
`android/gradle.properties` carries the IPv6 proxy systemProps;
Gradle 9.5.0-all is pre-seeded in `/home/hatch/.gradle`.
**Heap:** the Flutter template's `-Xmx8G` OOM-kills the Gradle daemon
on this 8 GB shared VM (first attempt died at 469 s) — the app pins
`-Xmx2G` + `workers.max=1` in `android/gradle.properties`. If the VM
is busy with other agents' jobs (e.g. a large ffmpeg render), stop
unrelated heavy processes first or the build may still OOM. The build
also auto-installs CMake 3.22.1 on first run (license accepted
automatically).

**Release-build failures hit by Worker 3 (both fixed, both in the
recipe now):**
1. **Daemon OOM-killed (build 1, 159 s):** the Gradle daemon (PID
   21989, from Worker 2's debug build) disappeared mid-build with no
   OutOfMemoryError and no heap dump — the kernel OOM-killer took it
   while the backend (~1.1 GB RSS) + a stale Kotlin daemon (~1.2 GB)
   were also resident and only ~1.7 GB was available. Fix: kill the
   orphaned Kotlin daemon by PID, stop the backend for the build, then
   rebuild with ~5.9 GB available.
2. **AGP lint crash (build 2, 8 m 7 s):** `lintVitalAnalyzeRelease`
   on `:google_maps_flutter_android` dies with
   `NoSuchMethodError: java.util.List.removeLast()` (a Java 21 API)
   inside lint's JavaDocParser on that plugin's `Convert.java` — a
   crash in the lint tool under JDK 17, not a finding in our code.
   Only JDK 17 exists on this VM. Fix: `checkReleaseBuilds = false`
   for all Android modules in `android/build.gradle.kts` (see the
   comment there — the block MUST stay above the
   `evaluationDependsOn(":app")` block or AGP rejects it as too
   late). Verified by dry-run: zero `lintVital*` tasks in the release
   graph. `flutter analyze` + unit tests remain the quality gate.
   Re-enable release lint if the toolchain moves to JDK 21 or a fixed
   AGP. Build 3 (warm caches) then succeeded in 117.5 s of Gradle time.

## Verification (Worker 3, 2026-11-16)

- `flutter doctor`: Flutter ✓, Android toolchain ✓ (SDK 36.0.0),
  Chrome ✓, Proxy ✓, Connected device ✓ (Linux + Chrome), Network ✓.
  Only failure is the known Linux toolchain (GTK 3.0 dev libs missing,
  uninstallable — see API_REFERENCE.md). Do NOT force-fix it.
  NOTE: a VM/cell migration mid-task removed `/usr/bin/google-chrome`;
  `flutter doctor -v` then reported Chrome ✗. Reinstalled from the
  cached `~/workspace/.tmp/google-chrome-stable_current_amd64.deb`
  (Chrome 157.0.8103.78, deps already satisfied) — if doctor ever
  reports Chrome missing again, that .deb is the restore.
- `flutter analyze`: **No issues found!** (see the deprecation note
  below — the suppression is gone and the analyzer stays strict).
- `flutter test`: **38/38 passed** — 27 original unit/widget tests +
  11 live-backend tests (`test/live_backend_test.dart`, new).
- **Live backend** (`test/live_backend_test.dart`, real client, real
  server at `http://127.0.0.1:8000`, no mocks): `/health` ok model v1
  MAE 2.145 / RMSE 3.307, data through 2026-10-29; `/predict_risk`
  cam 1413 @ 2026-10-15 → 53.0095 HIGH, hourly sum 53.0095, peak
  15:00 / 4.8194 HIGH; `/predict_risk/batch` 2 items, 0 errors;
  `/intersections` 300 cameras, first 1413 LAKE SHORE DR AND BELMONT
  60.54 HIGH; `/violation_trends` 1413, 3 days, total observed 163;
  `/live/risk` downtown → cam 3041 MICHIGAN AND JACKSON, 443 m,
  `adjustment_applied: false`, factor 1.0; Report Spike POST→GET
  round-trip; user alerts create→list→deactivate; settings round-trip;
  feedback POST; error paths (400 no-location, 404 mid-ocean) surface
  typed `ApiException`s. Test rows are marked `[Worker 3 live
  verification 2026-11-16]` under user `w3-live-test` in the dev
  SQLite DB (1 report + 1 alert + 1 feedback per run, 2 runs).
  One backend finding, reported not fixed: `/live/risk` rounds daily
  to 2 dp and hourly to 3 dp without renormalising, so its hourly
  array sums to 10.296 while `daily_prediction` reads 10.3 for cam
  3041 — a 0.004 (0.04%) display-rounding gap (`backend/routers/
  live.py:118` vs `:204`); `/predict_risk` reconciles exactly.
  Bands and display are unaffected; the backend was deliberately not
  touched for it.
- **Web (added by Worker 3 for verification):** `web/` did not exist,
  so `flutter create . --platforms=web` added it (nothing else
  changed — `pubspec.yaml` untouched). `flutter build web --release`
  succeeds (`✓ Built build/web`). The default CanvasKit build boots
  in headless Chrome 157 and the Map tab renders the full missing-key
  explanation (verified by screenshot: title, 6 steps, "No Maps key"
  chip, bottom nav — zero console errors). Two environment hurdles,
  both sandbox-only: the bundled `/opt/meta-chromium/chrome` refuses
  to start from a shell ("Hatch WebGL domain policy is required"),
  and headless Chrome rejects userinfo in `--proxy-server`
  (`ERR_NO_SUPPORTED_PROXIES`) so it could not reach gstatic for
  CanvasKit/fonts until a throwaway localhost CONNECT relay
  (`~/workspace/.tmp/w3_chrome_relay.py`, killed after) injected the
  proxy auth. On a normal network the web build just works. Note:
  `--web-renderer` no longer exists in Flutter 3.50, so the HTML
  renderer is not an option.
- **Missing-key path:** verified three ways — widget test (Map tab
  shows the explanation, never a blank map), Settings screen test
  ("Google Maps API key MISSING"), and the real-browser screenshot
  above. Query + Settings work without a key; Map + Route show the
  explanation. Worker 3 fixed one wrong sentence in that screen: it
  used to claim "The Query, Route and Settings tabs work without a
  key" even though Route also shows the explanation — it now reads
  "The Query and Settings tabs work without a key; only the map and
  route views need it."
- **Deprecation suppression — REMOVED (Worker 3):** Worker 2's
  project-wide `deprecated_member_use: ignore` is gone from
  `analysis_options.yaml`. The claim behind it is half true: this
  Flutter 3.50.0 SDK really does mark `package:flutter/material.dart`
  itself `@Deprecated` ("Use package:material_ui/material_ui.dart
  instead", after v3.47.0-0.0.pre — see
  `~/flutter/packages/flutter/lib/material.dart`), but no
  `material_ui` package exists anywhere in the SDK, so the standard
  import cannot be replaced. With the suppression removed, analyze
  reported 11 info issues: 10× the material.dart import (8 lib + 2
  test files) and 1× a REAL deprecation — `Color.withOpacity` in
  `lib/screens/map_screen.dart:438`. The real one is fixed
  (`.withValues(alpha: 0.2)`); each material.dart import now carries
  its own `// ignore: deprecated_member_use` so the analyzer stays
  strict for everything else. Analyze is clean with no project-wide
  suppression.

## iOS

Not built here: an iOS build needs macOS + an Apple Developer
account. The code is iOS-compatible in principle (google_maps_flutter,
geolocator, audioplayers all support iOS), but it has never been
compiled for iOS — **no IPA exists**.

iOS path (docs only — Gabriel submits):
1. Apple Developer account — $99/year (Apple Developer Program,
   developer.apple.com). Enrol as an individual; Gabriel's legal name
   and a payment method are needed. No account has been created.
2. A Mac (or a Mac cloud builder) with Xcode: `flutter build ipa
   --release` from this same project. The `ios/` folder does not exist
   yet — `flutter create . --platforms=ios` adds it, same as `web/`
   was added for verification. App ID: `com.redunator.redunator`
   (register the same bundle ID in the Developer account).
3. Maps key for iOS: the same key from `lib/config.dart` must also be
   enabled for **Maps SDK for iOS** in the Cloud console and added to
   `ios/Runner/AppDelegate.swift` (or `Info.plist`) per the
   google_maps_flutter iOS setup — the Android manifest injection in
   `android/app/build.gradle.kts` does not cover iOS.
4. Permissions: add `NSLocationWhenInUseUsageDescription` (and
   `NSLocationAlwaysAndWhenInUseUsageDescription` if background alerts
   are ever added) to `ios/Runner/Info.plist` — geolocator requires
   the usage strings or iOS rejects location access.
5. Signing + TestFlight: create the App Store Connect record, archive
   with the distribution certificate, upload the IPA to TestFlight
   (`flutter build ipa` produces an `.xcarchive`; upload via Xcode
   Organizer or Transporter), then invite testers. No store listing,
   screenshots, or review submission has been done.
6. Backend URL: the iOS default base URL is currently the Android
   emulator's `http://10.0.2.2:8000` — on a real iPhone that address
   means nothing; set the backend URL in Settings to the host's LAN IP
   or a deployed backend URL before testing on device.

## Permissions (Android — why each one)

Declared in `android/app/src/main/AndroidManifest.xml` (Flutter
template + plugin requirements):
- `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` — proximity
  alerts (geolocator): the app watches the user's position and warns
  near a high-risk camera. Foreground only; no background location
  permission is requested in this build (see below).
- `INTERNET` — all backend calls (`/predict_risk`, `/live/risk`,
  …) plus OSRM routing.
- `POST_NOTIFICATIONS` (Android 13+) — only relevant if alerts ever
  move to notifications; the current build alerts in-app with audio
  (audioplayers), not notifications.
- `FOREGROUND_SERVICE` — not requested; no foreground service runs.
  Background alerts are NOT implemented (see Settings screen text):
  alerts pause when the app is backgrounded or killed.

## Files

- `lib/config.dart` — Maps key + default base URL (single source of truth)
- `lib/api/client.dart` — typed client for all 14 backend operations
- `lib/api/models.dart` — typed models for every endpoint
- `lib/api/routing.dart` — OSRM primary + Google Directions fallback
- `lib/screens/map_screen.dart` — map + detail sheet + Report Spike
- `lib/screens/query_screen.dart` — intersection + hour query
- `lib/screens/route_screen.dart` — risk-coloured route
- `lib/screens/settings_screen.dart` — settings, health, feedback
- `lib/services/settings_service.dart` — local settings (shared_preferences)
- `lib/services/cache_service.dart` — offline cache (SDD FR12)
- `lib/services/proximity_service.dart` — foreground proximity alerts
- `lib/services/risk.dart` — bands, scores, marker colours
- `lib/widgets/hourly_chart.dart` — 24-hour curve (fl_chart)
- `lib/widgets/missing_key_view.dart` — missing-key explanation
- `test/` — API client (mock HTTP), risk bands, routing, proximity,
  widget tests, plus `live_backend_test.dart` (Worker 3: real client
  against the live backend; skips when the backend is not running)
- `web/` — added by Worker 3 (2026-11-16) for verification;
  `flutter build web --release` succeeds, CanvasKit renderer
