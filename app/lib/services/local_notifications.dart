// Local notifications for Redunator Pro: background alerts and the
// morning risk digest. LOCAL ONLY — no Firebase/FCM, no server push:
// the phone computes everything itself from data it fetches from the
// Redunator backend. Pro-gating lives in the callers
// (BackgroundAlertsService, MorningDigestService), never here.
//
// ONE CHANNEL PER ALERT KIND (apk-16): each kind has its own sound,
// so Gabriel can tell what happened without looking at the phone.
// Android fixes a channel's sound at creation, so a kind's sound
// can only change with a new channel id — these ids are versioned
// per kind for exactly that reason. The sound files live in
// android/app/src/main/res/raw/ (referenced by name, no extension)
// and in assets/ under the same name for in-app playback (see
// sound_service.dart — keep both copies in sync).
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest_all.dart' as tz;
import 'package:timezone/timezone.dart' as tz;

/// Proximity alert: a HIGH-risk camera within range. High
/// importance so it shows heads-up with sound.
const String kProximityChannelId = 'redunator_alert_proximity';

/// Spike digest: the daily digest, when it contains a spike
/// (>1.5x a saved camera's 7-day mean). High importance — a spike
/// day deserves the same urgency as a proximity alert.
const String kSpikeChannelId = 'redunator_alert_spike';

/// Area-watch alert: a HIGH camera inside a watched area
/// (Exclusive). High importance.
const String kAreaChannelId = 'redunator_alert_area';

/// Morning digest: the daily 07:00 summary, default importance.
const String kDailyDigestChannelId = 'redunator_digest_daily';

/// Weekly digest: the Sunday 07:00 summary, default importance.
const String kWeeklyDigestChannelId = 'redunator_digest_weekly';

// Retired in apk-16 (replaced by the per-kind channels above).
// Deleted on init so stale channels do not linger in system
// settings on phones that already created them.
const String kLegacyAlertChannelId = 'redunator_alerts';
const String kLegacyDigestChannelId = 'redunator_digest';

/// Fixed id for the daily digest notification — rescheduling with
/// the same id replaces it. Background alerts use per-camera ids
/// (see showBackgroundAlert) so two cameras do not clobber each
/// other.
const int kDigestNotificationId = 1001;

/// Fixed id for the weekly (Sunday) digest notification.
const int kWeeklyDigestNotificationId = 1002;

/// Smallest Android notification id used for background alerts.
/// The id is this base + (cameraId.hashCode % 100000).
const int kBackgroundAlertIdBase = 2000;

/// Smallest Android notification id used for area-watch alerts.
/// The id is this base + (areaId.hashCode % 100000).
const int kAreaAlertIdBase = 3000;

/// One pending scheduled notification, as reported by the plugin.
/// The plugin does not report the scheduled fire time, so the
/// scheduled-for time comes from the last schedule-attempt record
/// (see MorningDigestService).
class PendingNotificationSummary {
  const PendingNotificationSummary({
    required this.id,
    required this.title,
    required this.body,
  });
  final int id;
  final String? title;
  final String? body;
}

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
  /// [hasSpike] routes a daily digest containing a spike to the
  /// spike channel (its own sound); [weekly] routes a weekly digest
  /// to the weekly channel.
  Future<void> showNow({
    required String title,
    required String body,
    bool hasSpike = false,
    bool weekly = false,
  });

  /// Schedules the daily digest at [hour]:00 device-local time,
  /// repeating every day. Replaces any previously scheduled digest.
  /// [hasSpike] routes it to the spike channel (its own sound).
  Future<void> scheduleDailyDigest({
    required String title,
    required String body,
    required int hour,
    bool hasSpike = false,
  });

  Future<void> cancelDigest();

  /// Shows an area-watch alert right now (Exclusive): a HIGH camera
  /// inside one of the user's watched areas.
  Future<void> showAreaAlert({
    required String areaId,
    required String areaName,
    required String body,
  });

  /// Schedules the weekly digest on [weekday] at [hour]:00
  /// device-local time, repeating every week. Replaces any
  /// previously scheduled weekly digest.
  Future<void> scheduleWeeklyDigest({
    required String title,
    required String body,
    required int weekday,
    required int hour,
  });

  Future<void> cancelWeeklyDigest();

  /// Android 13+ runtime notification permission. True when granted
  /// (or when the platform has no such permission).
  Future<bool> requestNotificationPermission();

  /// True when the app may schedule exact alarms (Android 12+).
  /// The digest falls back to inexact scheduling when false.
  Future<bool> canScheduleExactAlarms();

  /// Scheduled notifications still pending with the plugin, limited
  /// to the digest ids (daily 1001, weekly 1002). Empty when no
  /// digest alarm is currently scheduled — the key fact the Settings
  /// diagnostics needs after a missed 07:00.
  Future<List<PendingNotificationSummary>> pendingDigestNotifications();

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
    // One channel per alert kind, each with its own sound file
    // (res/raw, name without extension).
    const channels = [
      (
        kProximityChannelId,
        'High-risk camera nearby',
        'A HIGH-risk camera is within range',
        Importance.high,
        'proximity_alert',
      ),
      (
        kSpikeChannelId,
        'Spike in the morning digest',
        'A saved camera is spiking above its 7-day mean',
        Importance.high,
        'spike_alert',
      ),
      (
        kAreaChannelId,
        'High-risk camera in a watched area',
        'A HIGH camera is inside a watched area',
        Importance.high,
        'area_alert',
      ),
      (
        kDailyDigestChannelId,
        'Redunator morning digest',
        'Daily 07:00 risk summary for saved intersections',
        Importance.defaultImportance,
        'digest_morning',
      ),
      (
        kWeeklyDigestChannelId,
        'Redunator weekly digest',
        'Sunday 07:00 review of the saved intersections’ week',
        Importance.defaultImportance,
        'digest_weekly',
      ),
    ];
    for (final (id, name, description, importance, sound) in channels) {
      await android?.createNotificationChannel(
        AndroidNotificationChannel(
          id,
          name,
          description: description,
          importance: importance,
          sound: RawResourceAndroidNotificationSound(sound),
        ),
      );
    }
    // Retire the pre-apk-16 shared channels so they stop showing in
    // system settings. Safe to call when they were never created.
    await android?.deleteNotificationChannel(
      channelId: kLegacyAlertChannelId,
    );
    await android?.deleteNotificationChannel(
      channelId: kLegacyDigestChannelId,
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
          kProximityChannelId,
          'High-risk camera nearby',
          channelDescription: 'A HIGH-risk camera is within range',
          importance: Importance.high,
          priority: Priority.high,
          sound: const RawResourceAndroidNotificationSound(
            'proximity_alert',
          ),
        ),
      ),
    );
  }

  @override
  Future<void> showNow({
    required String title,
    required String body,
    bool hasSpike = false,
    bool weekly = false,
  }) async {
    // Route the test digest to the channel matching its kind, so
    // the sound heard is the sound the real digest will make.
    var channelId = kDailyDigestChannelId;
    var channelName = 'Redunator morning digest';
    var channelDescription =
        'Daily 07:00 risk summary for saved intersections';
    var sound = 'digest_morning';
    var importance = Importance.defaultImportance;
    if (weekly) {
      channelId = kWeeklyDigestChannelId;
      channelName = 'Redunator weekly digest';
      channelDescription =
          'Sunday 07:00 review of the saved intersections’ week';
      sound = 'digest_weekly';
    } else if (hasSpike) {
      channelId = kSpikeChannelId;
      channelName = 'Spike in the morning digest';
      channelDescription =
          'A saved camera is spiking above its 7-day mean';
      sound = 'spike_alert';
      importance = Importance.high;
    }
    await _plugin.show(
      id: kDigestNotificationId,
      title: title,
      body: body,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          channelId,
          channelName,
          channelDescription: channelDescription,
          importance: importance,
          priority: Priority.defaultPriority,
          sound: RawResourceAndroidNotificationSound(sound),
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
    bool hasSpike = false,
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
    final channelId = hasSpike ? kSpikeChannelId : kDailyDigestChannelId;
    final channelName = hasSpike
        ? 'Spike in the morning digest'
        : 'Redunator morning digest';
    final channelDescription = hasSpike
        ? 'A saved camera is spiking above its 7-day mean'
        : 'Daily 07:00 risk summary for saved intersections';
    final sound = hasSpike ? 'spike_alert' : 'digest_morning';
    await _plugin.zonedSchedule(
      id: kDigestNotificationId,
      title: title,
      body: body,
      scheduledDate: scheduled,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          channelId,
          channelName,
          channelDescription: channelDescription,
          importance: hasSpike
              ? Importance.high
              : Importance.defaultImportance,
          priority: Priority.defaultPriority,
          sound: RawResourceAndroidNotificationSound(sound),
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
  Future<void> showAreaAlert({
    required String areaId,
    required String areaName,
    required String body,
  }) async {
    final id = kAreaAlertIdBase + (areaId.hashCode.abs() % 100000);
    await _plugin.show(
      id: id,
      title: 'High-risk camera in $areaName',
      body: body,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          kAreaChannelId,
          'High-risk camera in a watched area',
          channelDescription: 'A HIGH camera is inside a watched area',
          importance: Importance.high,
          priority: Priority.high,
          sound: const RawResourceAndroidNotificationSound('area_alert'),
          styleInformation: BigTextStyleInformation(body),
        ),
      ),
    );
  }

  @override
  Future<void> scheduleWeeklyDigest({
    required String title,
    required String body,
    required int weekday,
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
    // Advance to the next occurrence of the requested weekday.
    var daysAhead = (weekday - scheduled.weekday) % 7;
    scheduled = scheduled.add(Duration(days: daysAhead));
    if (!scheduled.isAfter(now)) {
      scheduled = scheduled.add(const Duration(days: 7));
    }
    final exact = await canScheduleExactAlarms();
    await _plugin.zonedSchedule(
      id: kWeeklyDigestNotificationId,
      title: title,
      body: body,
      scheduledDate: scheduled,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          kWeeklyDigestChannelId,
          'Redunator weekly digest',
          channelDescription:
              'Sunday 07:00 review of the saved intersections’ week',
          importance: Importance.defaultImportance,
          priority: Priority.defaultPriority,
          sound: const RawResourceAndroidNotificationSound(
            'digest_weekly',
          ),
          styleInformation: BigTextStyleInformation(body),
        ),
      ),
      androidScheduleMode: exact
          ? AndroidScheduleMode.exactAllowWhileIdle
          : AndroidScheduleMode.inexactAllowWhileIdle,
      matchDateTimeComponents: DateTimeComponents.dayOfWeekAndTime,
    );
  }

  @override
  Future<void> cancelWeeklyDigest() =>
      _plugin.cancel(id: kWeeklyDigestNotificationId);

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
  Future<List<PendingNotificationSummary>> pendingDigestNotifications() async {
    try {
      final pending = await _plugin.pendingNotificationRequests();
      return pending
          .where(
            (r) =>
                r.id == kDigestNotificationId ||
                r.id == kWeeklyDigestNotificationId,
          )
          .map(
            (r) => PendingNotificationSummary(
              id: r.id,
              title: r.title,
              body: r.body,
            ),
          )
          .toList();
    } catch (_) {
      return [];
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
