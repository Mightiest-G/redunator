// Redunator configuration.
//
// GOOGLE MAPS API KEY — THE ONLY place the key lives in this app.
// ------------------------------------------------------------------
// 1. Copy this file's value into a real key:
//      const String googleMapsApiKey = 'AIza...';
//    Get a key in the Google Cloud console:
//      Cloud Console -> APIs & Services -> Credentials -> Create Credentials
//      -> API key, then restrict it (Android apps, package
//      com.redunator.redunator) and enable "Maps SDK for Android" and
//      "Directions API" (Directions is only used as a fallback if OSRM
//      routing fails — see lib/api/routing.dart).
// 2. Rebuild the app. The same value is read by
//    android/app/build.gradle.kts (regex over this file) and injected
//    into AndroidManifest.xml as com.google.android.geo.API_KEY.
// 3. Do NOT commit a real key. Keep this file local, or use a build
//    variant that injects it at build time.
//
// When the key is empty, the app does NOT crash and does NOT show a
// blank map: the Map tab shows a full-screen explanation with these
// steps instead (see lib/widgets/missing_key_view.dart).
const String googleMapsApiKey = 'AIzaSyCiCbbOvejHZgOREi9d4JX0X1DXbZ3co3Q';

/// Default backend base URL: the live Cloud Run service
/// (https://redunator-api-108691448805.us-central1.run.app).
/// Overridable in Settings; a saved Settings value wins over this.
/// For local development use the emulator address
/// http://10.0.2.2:8000, or a LAN IP for a physical device.
const String defaultBaseUrl =
    'https://redunator-api-108691448805.us-central1.run.app';

/// Whether the key above is present. Used by the UI to decide between
/// the real map and the missing-key explanation.
bool get hasMapsApiKey => googleMapsApiKey.isNotEmpty;
