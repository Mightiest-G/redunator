// Local notifications for Redunator Pro: background alerts and the
// morning risk digest. LOCAL ONLY — no Firebase/FCM, no server push:
// the phone computes everything itself from data it fetches from the
// Redunator backend. Pro-gating lives in the callers
// (BackgroundAlertsService, MorningDigestService), never here.
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest_all.dart' as tz;
import 'package:timezone/timezone.dart' as tz;

/// Notification channel for background proximity alerts: high
/// importance so it shows heads-up with sound.
const String kAlertChannelId = 'redunator_alerts';

/// Notification channel for the morning digest: a quiet daily
/// summary, default importance.
const String kDigestChannelId = 'redunator_digest';

/// Fixed id for the daily digest notification — rescheduling with
/// the same id replaces it. Background alerts use per-camera ids
/// (see showBackgroundAlert) so two cameras do not clobber each
/// other.
const int kDigestNotificationId = 1001;

/// Smallest Android notification id used for background alerts.
/// The id is this base + (cameraId.hashCode % 100000).
const int kBackgroundAlertIdBase = 2000;

/// Gateway seam so services and tests can fake notifications without
/// touching platform channels.
abstract class NotificationGateway {
  Future<void> showBackgroundAlert({
    required String cameraId,
    required String intersection,
    required double distanceM,
    required String band,
    required double dailyPrediction,
  });

  /// Shows a notification right now (used for the digest test send).
  Future<void> showNow({required String title, required String body});

  /// Schedules the daily digest at [hour]:00 device-local time,
  /// repeating every day. Replaces any previously scheduled digest.
  Future<void> scheduleDailyDigest({
    required String title,
    required String body,
    required int hour,
  });

  Future<void> cancelDigest();

  /// Android 13+ runtime notification permission. True when granted
  /// (or when the platform has no such permission).
  Future<bool> requestNotificationPermission();

  /// True when the app may schedule exact alarms (Android 12+).
  /// The digest falls back to inexact scheduling when false.
  Future<bool> canScheduleExactAlarms();

  /// Asks Android 12+ for the exact-alarm permission. Best-effort:
  /// a denial just means the digest uses inexact scheduling.
  Future<void> requestExactAlarmsPermission();
}

class LocalNotifications implements NotificationGateway {
  LocalNotifications([FlutterLocalNotificationsPlugin? plugin])
    : _plugin = plugin ?? FlutterLocalNotificationsPlugin();

  final FlutterLocalNotificationsPlugin _plugin;
  bool _timeZoneInitialized = false;

  /// Loads the IANA timezone database and sets the device-local zone.
  /// Must run before any zoned scheduling, in the foreground app and
  /// in the background isolate alike (each isolate initialises once).
  /// Failures are swallowed: scheduling then falls back to UTC, which
  /// is still a daily 07:00 — just in the wrong zone — rather than
  /// crashing the caller.
  static Future<void> initTimeZone() async {
    try {
      tz.initializeTimeZones();
      final info = await FlutterTimezone.getLocalTimezone();
      tz.setLocalLocation(tz.getLocation(info.identifier));
    } catch (_) {
      // Leave tz.local at its default; scheduling still works.
    }
  }

  /// Idempotent per instance: safe to call before scheduling from
  /// any isolate that has not initialised the timezone yet.
  Future<void> ensureTimeZone() async {
    if (_timeZoneInitialized) return;
    await initTimeZone();
    _timeZoneInitialized = true;
  }

  /// Creates the channels and registers the plugin. Does NOT request
  /// any permission — that happens from Settings, on a user gesture.
  Future<void> init() async {
    const androidSettings = AndroidInitializationSettings(
      '@mipmap/ic_launcher',
    );
    const settings = InitializationSettings(android: androidSettings);
    await _plugin.initialize(settings: settings);
    final android = _plugin
        .resolvePlatformSpecificImplementation<
          AndroidFlutterLocalNotificationsPlugin
        >();
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        kAlertChannelId,
        'Redunator alerts',
        description: 'High-risk camera proximity alerts',
        importance: Importance.high,
      ),
    );
    await android?.createNotificationChannel(
      const AndroidNotificationChannel(
        kDigestChannelId,
        'Redunator morning digest',
        description: 'Daily 07:00 risk summary for saved intersections',
        importance: Importance.defaultImportance,
      ),
    );
  }

  AndroidFlutterLocalNotificationsPlugin? get _android =>
      _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin
      >();

  @override
  Future<void> showBackgroundAlert({
    required String cameraId,
    required String intersection,
    required double distanceM,
    required String band,
    required double dailyPrediction,
  }) async {
    final id =
        kBackgroundAlertIdBase + (cameraId.hashCode.abs() % 100000);
    await _plugin.show(
      id: id,
      title: 'High-risk camera nearby',
      body:
          '$intersection — ${dailyPrediction.toStringAsFixed(1)}/day '
          '($band), ${_formatDistance(distanceM)} away. Estimates.',
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          kAlertChannelId,
          'Redunator alerts',
          channelDescription: 'High-risk camera proximity alerts',
          importance: Importance.high,
          priority: Priority.high,
        ),
      ),
    );
  }

  @override
  Future<void> showNow({
    required String title,
    required String body,
  }) async {
    await _plugin.show(
      id: kDigestNotificationId,
      title: title,
      body: body,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          kDigestChannelId,
          'Redunator morning digest',
          channelDescription:
              'Daily 07:00 risk summary for saved intersections',
          importance: Importance.defaultImportance,
          priority: Priority.defaultPriority,
          // BigText so the multi-line digest is fully readable when
          // the notification is expanded.
          styleInformation: BigTextStyleInformation(body),
        ),
      ),
    );
  }

  @override
  Future<void> scheduleDailyDigest({
    required String title,
    required String body,
    required int hour,
  }) async {
    await ensureTimeZone();
    final now = tz.TZDateTime.now(tz.local);
    var scheduled = tz.TZDateTime(
      tz.local,
      now.year,
      now.month,
      now.day,
      hour,
    );
    if (!scheduled.isAfter(now)) {
      scheduled = scheduled.add(const Duration(days: 1));
    }
    final exact = await canScheduleExactAlarms();
    await _plugin.zonedSchedule(
      id: kDigestNotificationId,
      title: title,
      body: body,
      scheduledDate: scheduled,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          kDigestChannelId,
          'Redunator morning digest',
          channelDescription:
              'Daily 07:00 risk summary for saved intersections',
          importance: Importance.defaultImportance,
          priority: Priority.defaultPriority,
          // BigText so the multi-line digest is fully readable when
          // the notification is expanded.
          styleInformation: BigTextStyleInformation(body),
        ),
      ),
      androidScheduleMode: exact
          ? AndroidScheduleMode.exactAllowWhileIdle
          : AndroidScheduleMode.inexactAllowWhileIdle,
      matchDateTimeComponents: DateTimeComponents.time,
    );
  }

  @override
  Future<void> cancelDigest() => _plugin.cancel(id: kDigestNotificationId);

  @override
  Future<bool> requestNotificationPermission() async {
    final android = _android;
    if (android == null) return true;
    try {
      return await android.requestNotificationsPermission() ?? false;
    } catch (_) {
      return false;
    }
  }

  /// Whether notifications are currently allowed (Android 13+).
  Future<bool> notificationsEnabled() async {
    final android = _android;
    if (android == null) return true;
    try {
      return await android.areNotificationsEnabled() ?? false;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<bool> canScheduleExactAlarms() async {
    final android = _android;
    if (android == null) return false;
    try {
      return await android.canScheduleExactNotifications() ?? false;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<void> requestExactAlarmsPermission() async {
    try {
      await _android?.requestExactAlarmsPermission();
    } catch (_) {
      // Denial just means inexact scheduling (see scheduleDailyDigest).
    }
  }

  static String _formatDistance(double metres) {
    if (metres >= 1000) return '${(metres / 1000).toStringAsFixed(2)} km';
    return '${metres.round()} m';
  }
}
