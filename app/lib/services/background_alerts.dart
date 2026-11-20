// Background proximity alerts (Pro) + area watch (Exclusive). A
// WorkManager periodic task (15 minutes — the Android minimum) runs
// while the app is closed:
//   - Pro: reads the current location, asks the backend for the
//     nearest camera (GET /live/risk), and fires a LOCAL notification
//     when the user is within their Pro alert distance of a HIGH-risk
//     camera.
//   - Exclusive: also evaluates every watched area — any HIGH camera
//     inside an area alerts even when the user is nowhere near it.
// No Firebase/FCM, no server push — the phone does the check itself.
// The task is a no-op for free users and is cancelled outright
// when the tier drops to free. The same run also refreshes the
// morning digest schedule, the weekly digest and the home-screen
// widget, which is how they survive a reboot (WorkManager re-runs
// periodic work after boot).
//
// PLATFORM LIMITS (documented honestly — do NOT claim more):
// - The check needs location "Allow all the time" (Android 10+):
//   without it, getCurrentPosition fails in the background and the
//   run does nothing. Android 11+ only grants "all the time" from
//   Settings — the Settings screen offers to open them.
// - WorkManager runs are best-effort: Android may defer a 15-minute
//   run (Doze, battery saver), so alerts can arrive late. The
//   foreground watcher is still the prompt one.
// - iOS is not a target for this app (no iOS build exists); the
//   WorkManager task is Android-only in practice.
import 'dart:async';
import 'dart:ui';

import 'package:geolocator/geolocator.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:workmanager/workmanager.dart';

import '../api/client.dart';
import '../api/models.dart';
import '../config.dart';
import 'area_watch_service.dart';
import 'local_notifications.dart';
import 'morning_digest.dart';
import 'settings_service.dart';
import 'subscription_service.dart';
import 'widget_service.dart';

/// Unique name of the periodic WorkManager task (one task does the
/// background alert check AND the digest refresh).
const String kBackgroundTaskUniqueName = 'redunator-background-check';

/// Task name delivered to the background callback dispatcher.
const String kBackgroundTaskName = 'redunator.background-check';

/// WorkManager's minimum periodic interval on Android.
const Duration kBackgroundCheckInterval = Duration(minutes: 15);

/// SharedPreferences keys for the background-alerts toggle. These
/// mirror AppSettings ('background_alerts_enabled'); the background
/// isolate reads them directly because AppSettings lives in the
/// foreground app.
const String kBackgroundAlertsEnabledKey = 'background_alerts_enabled';

/// Pure rule: the nearest camera triggers a background alert when it
/// is within [alertDistanceM] and its DAILY band is HIGH. (The
/// foreground watcher uses the risk-threshold slider instead; the
/// background rule is the HIGH band, as specified.) Returns the
/// LiveRisk when it should alert, else null.
LiveRisk? backgroundAlertTarget(
  LiveRisk live, {
  required double alertDistanceM,
}) {
  if (live.distanceM > alertDistanceM) return null;
  if (live.dailyBand != 'HIGH') return null;
  return live;
}

/// At most one background alert per camera per calendar day (device
/// local date). Backed by SharedPreferences so the dedupe survives
/// the background isolate being killed between runs.
class DailyAlertDedupe {
  DailyAlertDedupe(this._prefs);

  final SharedPreferences _prefs;

  static String _key(String cameraId) => 'bg_alert_last_$cameraId';

  /// True when this camera has not alerted today yet.
  bool shouldAlert(String cameraId, DateTime now) =>
      _prefs.getString(_key(cameraId)) != dateKey(now);

  Future<void> recordAlert(String cameraId, DateTime now) async {
    await _prefs.setString(_key(cameraId), dateKey(now));
  }
}

/// Permission state shown on the Settings screen.
class BackgroundPermissionState {
  BackgroundPermissionState({
    required this.notificationsGranted,
    required this.location,
  });

  /// Android 13+ notification permission.
  final bool notificationsGranted;

  /// Geolocator permission (denied / whileInUse / always / ...).
  final LocationPermission location;

  bool get locationAlways => location == LocationPermission.always;

  bool get ready => notificationsGranted && locationAlways;
}

/// Seam over WorkManager so tests can fake scheduling without
/// touching platform channels.
abstract class BackgroundTaskScheduler {
  Future<void> registerPeriodic();
  Future<void> cancel();
}

class WorkmanagerScheduler implements BackgroundTaskScheduler {
  @override
  Future<void> registerPeriodic() => Workmanager().registerPeriodicTask(
    kBackgroundTaskUniqueName,
    kBackgroundTaskName,
    frequency: kBackgroundCheckInterval,
    // No point running without a network: the check is one backend
    // call. WorkManager retries the run when a network appears.
    constraints: Constraints(networkType: NetworkType.connected),
    existingWorkPolicy: ExistingPeriodicWorkPolicy.keep,
  );

  @override
  Future<void> cancel() =>
      Workmanager().cancelByUniqueName(kBackgroundTaskUniqueName);
}

class BackgroundAlertsService {
  BackgroundAlertsService({
    BackgroundTaskScheduler? scheduler,
    NotificationGateway? notifications,
    this._positionOverride,
    RedunatorApiClient Function({required String baseUrl})? clientFactory,
  }) : _scheduler = scheduler ?? WorkmanagerScheduler(),
       _notifications = notifications ?? LocalNotifications(),
       _clientFactory =
           clientFactory ??
           (({required String baseUrl}) =>
               RedunatorApiClient(baseUrl: baseUrl));

  final BackgroundTaskScheduler _scheduler;
  final NotificationGateway _notifications;
  final Future<Position?> Function()? _positionOverride;
  final RedunatorApiClient Function({required String baseUrl})
  _clientFactory;

  /// Registers WorkManager's background entry point. Call once from
  /// main(), before runApp.
  static Future<void> initializeWorkmanager() async {
    await Workmanager().initialize(backgroundAlertsCallbackDispatcher);
  }

  /// Mirrors SubscriptionService's storage ('subscription_tier', with
  /// the legacy 'is_pro' fallback, and 'pro_expiry_ms'): true only
  /// when a paid tier is cached and not expired. The background
  /// isolate cannot use SubscriptionService (it needs a BillingStore),
  /// so it reads the same keys directly.
  static bool isProActive(SharedPreferences prefs) {
    final tier = prefs.getString(SubscriptionService.tierPrefsKey);
    if (tier != null) {
      if (tier == SubscriptionTier.free.name) return false;
      return !_expired(prefs);
    }
    if (prefs.getBool('is_pro') != true) return false;
    return !_expired(prefs);
  }

  /// True only when the cached tier is Exclusive and not expired.
  /// There is no legacy equivalent (Exclusive did not exist before
  /// tiers), so a missing tier key means false.
  static bool isExclusiveActive(SharedPreferences prefs) {
    if (prefs.getString(SubscriptionService.tierPrefsKey) !=
        SubscriptionTier.exclusive.name) {
      return false;
    }
    return !_expired(prefs);
  }

  static bool _expired(SharedPreferences prefs) {
    final expiryMs = prefs.getInt('pro_expiry_ms');
    return expiryMs != null &&
        expiryMs < DateTime.now().millisecondsSinceEpoch;
  }

  /// Registers the 15-minute background task when Pro is on and the
  /// user enabled background alerts; cancels it otherwise. Call
  /// whenever isPro or the toggle changes. Throws on scheduler
  /// failure — callers (main, Settings) catch it so a scheduling
  /// failure never breaks the UI.
  Future<void> syncWithPro({
    required bool isPro,
    required bool enabled,
  }) async {
    if (isPro && enabled) {
      await _scheduler.registerPeriodic();
    } else {
      await _scheduler.cancel();
    }
  }

  /// Current permission state for the Settings screen.
  Future<BackgroundPermissionState> permissionStatus() async {
    final notifications = _notifications is LocalNotifications
        ? await _notifications.notificationsEnabled()
        : true;
    final location = await Geolocator.checkPermission();
    return BackgroundPermissionState(
      notificationsGranted: notifications,
      location: location,
    );
  }

  /// Runs the permission flow: notification permission, then
  /// foreground location. Android 11+ cannot grant "Allow all the
  /// time" from a runtime prompt — when the result is only
  /// whileInUse, the Settings screen offers to open app settings.
  Future<BackgroundPermissionState> requestPermissions() async {
    await _notifications.requestNotificationPermission();
    var permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }
    final notifications = _notifications is LocalNotifications
        ? await _notifications.notificationsEnabled()
        : true;
    return BackgroundPermissionState(
      notificationsGranted: notifications,
      location: permission,
    );
  }

  /// Asks Android 12+ for the exact-alarm permission (makes the
  /// 07:00 digest land on time). Best-effort: a denial just falls
  /// back to inexact scheduling.
  Future<void> requestExactAlarmsPermission() =>
      _notifications.requestExactAlarmsPermission();

  /// Opens the OS app-settings page (where "Allow all the time" is
  /// granted on Android 11+).
  Future<bool> openAppSettings() => Geolocator.openAppSettings();

  /// The work one background run does. Reads everything from
  /// SharedPreferences (Pro state, toggle, alert distance, base
  /// URL), so it works in the background isolate with no app state.
  /// Never throws: a failed run just means no alert this cycle.
  /// Test seams: [positionOverride] and [clientFactory] replace the
  /// device location and the HTTP client.
  Future<void> runBackgroundCheck() async {
    final prefs = await SharedPreferences.getInstance();
    if (!isProActive(prefs)) return;
    // The toggle defaults to ON (AppSettings.default): unset means
    // enabled; only an explicit false turns background alerts off.
    if (prefs.getBool(kBackgroundAlertsEnabledKey) == false) return;
    final alertDistance =
        prefs.getDouble('pro_alert_distance_m') ??
        AppSettings.defaultProAlertDistanceM;
    final baseUrl = prefs.getString('base_url') ?? defaultBaseUrl;
    final client = _clientFactory(baseUrl: baseUrl);
    try {
      final position = await _currentPosition();
      if (position == null) return;
      LiveRisk? live;
      try {
        live = await client.getLiveRisk(
          latitude: position.latitude,
          longitude: position.longitude,
          // /live/risk requires 100..20000; Pro distances are
          // 100/250/500/1000, so this always validates.
          maxDistanceM: alertDistance.round(),
        );
      } on ApiException catch (e) {
        // 404 = no camera within the alert distance: not an error,
        // just no alert this run. Other failures are swallowed too
        // (the digest refresh below still gets its chance).
        if (e.statusCode != 404) return;
        live = null;
      }
      final target = live == null
          ? null
          : backgroundAlertTarget(live, alertDistanceM: alertDistance);
      if (target != null) {
        final dedupe = DailyAlertDedupe(prefs);
        final now = DateTime.now();
        if (dedupe.shouldAlert(target.cameraId, now)) {
          await _notifications.showBackgroundAlert(
            cameraId: target.cameraId,
            intersection: target.intersection ?? target.cameraId,
            distanceM: target.distanceM,
            band: target.dailyBand,
            dailyPrediction: target.dailyPrediction,
          );
          await dedupe.recordAlert(target.cameraId, now);
        }
      }
      // Area watch (Exclusive): HIGH cameras inside any watched area,
      // even when the user is nowhere near them.
      if (isExclusiveActive(prefs)) {
        await _checkWatchAreas(prefs, client);
      }
    } finally {
      // Refresh the morning digest schedule with fresh data, even
      // when the alert check found nothing: this is also what
      // re-schedules the digest after a reboot.
      try {
        await MorningDigestService(
          notifications: _notifications,
        ).ensureScheduled(
          client: client,
          isPro: true,
          enabled: prefs.getBool(kDigestEnabledKey) ?? true,
        );
      } catch (_) {
        // Best-effort only.
      }
      // Weekly digest (Exclusive) + home-screen widget (Exclusive):
      // same refresh, same reboot survival.
      try {
        await MorningDigestService(
          notifications: _notifications,
        ).ensureWeeklyScheduled(
          client: client,
          isExclusive: isExclusiveActive(prefs),
          enabled: prefs.getBool(kWeeklyDigestEnabledKey) ?? true,
        );
      } catch (_) {}
      try {
        if (isExclusiveActive(prefs)) {
          await WidgetService().refresh(client: client);
        }
      } catch (_) {}
      client.close();
    }
  }

  /// Area-watch check for one background run: loads the persisted
  /// areas, fetches the camera list, batch-predicts the cameras in
  /// any area, and notifies once per area per day when a HIGH camera
  /// is inside. Failures are swallowed — a missed run just means no
  /// area alert this cycle.
  Future<void> _checkWatchAreas(
    SharedPreferences prefs,
    RedunatorApiClient client,
  ) async {
    try {
      final areas = AreaWatchService.readPersisted(prefs);
      if (areas.isEmpty) return;
      final cameras = await client.getIntersections(limit: 300);
      final inAnyArea = cameras
          .where(
            (c) => areas.any(
              (a) => camerasInArea([c], a).isNotEmpty,
            ),
          )
          .toList();
      if (inAnyArea.isEmpty) return;
      final batch = await client.predictRiskBatch(
        inAnyArea.map((c) => {'camera_id': c.cameraId}).toList(),
      );
      final preds = {for (final p in batch.results) p.cameraId: p};
      final alerts = areaAlertTargets(
        areas: areas,
        cameras: cameras,
        predictions: preds,
      );
      final dedupe = AreaAlertDedupe(prefs);
      final now = DateTime.now();
      for (final alert in alerts) {
        if (!dedupe.shouldAlert(alert.area.id, now)) continue;
        await _notifications.showAreaAlert(
          areaId: alert.area.id,
          areaName: alert.area.name,
          body: buildAreaAlertText(alert),
        );
        await dedupe.recordAlert(alert.area.id, now);
      }
    } catch (_) {
      // Best-effort only.
    }
  }

  Future<Position?> _currentPosition() async {
    final override = _positionOverride;
    if (override != null) return override();
    try {
      final permission = await Geolocator.checkPermission();
      if (permission != LocationPermission.always &&
          permission != LocationPermission.whileInUse) {
        return null;
      }
      return await Geolocator.getCurrentPosition().timeout(
        const Duration(seconds: 30),
      );
    } catch (_) {
      return null;
    }
  }
}

/// WorkManager entry point. Must be top-level. Runs in a background
/// isolate: re-initialises plugins, timezone and notifications, then
/// runs the Pro background check. Never throws.
@pragma('vm:entry-point')
void backgroundAlertsCallbackDispatcher() {
  Workmanager().executeTask((task, inputData) async {
    try {
      DartPluginRegistrant.ensureInitialized();
      await LocalNotifications.initTimeZone();
      final notifications = LocalNotifications();
      await notifications.init();
      await BackgroundAlertsService(
        notifications: notifications,
      ).runBackgroundCheck();
      return true;
    } catch (_) {
      return false;
    }
  });
}
