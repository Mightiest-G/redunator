// Morning risk digest (Pro): a daily local notification at 07:00
// device-local time summarising today's predicted risk for the
// user's SAVED intersections, plus the safest time to leave (from
// the same hourly-curve math as the Route tab's safest-departure).
// All values are ESTIMATES and the text says so. Local only — no
// Firebase/FCM, no server push: the phone fetches the predictions
// itself and schedules the notification with flutter_local_notifications.
// Pro-gated: only scheduled while isPro (and the digest toggle) is
// on; cancelled when Pro turns off. No saved intersections -> the
// digest says so and suggests saving some (never an empty digest).
//
// The saved list is read from SharedPreferences (the persisted source
// of truth) so the same path works from the foreground app and from
// the WorkManager background task, which also refreshes the digest
// after a reboot.
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';
import 'local_notifications.dart';
import 'route_risk.dart';
import 'saved_service.dart';
import 'trend_analysis.dart';
import 'weekly_report.dart';

/// Digest time, device-local.
const int kDigestHour = 7;

/// Weekly digest: Sunday at 07:00 device-local.
const int kWeeklyDigestWeekday = DateTime.sunday;
const int kWeeklyDigestHour = 7;

/// SharedPreferences key holding the date (yyyy-MM-dd, device-local)
/// of the Sunday the weekly digest was last scheduled for.
/// Scheduling is skipped while the stored date is the upcoming
/// Sunday, so app starts and background runs do not re-fetch every
/// time.
const String kWeeklyDigestScheduledDateKey = 'weekly_digest_scheduled_date';

/// SharedPreferences key for the weekly digest toggle (AppSettings
/// mirrors this key; the background isolate reads it directly).
const String kWeeklyDigestEnabledKey = 'weekly_digest_enabled';

/// Date of the next [weekday] at [hour] (device-local), strictly
/// after [now].
DateTime nextWeekday(DateTime now, int weekday, int hour) {
  var d = DateTime(now.year, now.month, now.day, hour);
  final daysAhead = (weekday - d.weekday) % 7;
  d = d.add(Duration(days: daysAhead));
  if (!d.isAfter(now)) d = d.add(const Duration(days: 7));
  return d;
}
/// How many saved cameras the digest lists before collapsing the
/// rest into an "and N more" line (notification bodies are short).
const int kDigestMaxRows = 3;

/// SharedPreferences key holding the date (yyyy-MM-dd, device-local)
/// the digest was last scheduled for. Scheduling is skipped while
/// the stored date is today, so app starts and background runs do
/// not re-fetch every time.
const String kDigestScheduledDateKey = 'morning_digest_scheduled_date';

/// SharedPreferences key for the digest toggle (AppSettings mirrors
/// this key; the background isolate reads it directly).
const String kDigestEnabledKey = 'morning_digest_enabled';

/// Device-local calendar date as yyyy-MM-dd. Shared with the
/// background-alert dedupe (one alert per camera per day).
String dateKey(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

class DigestRow {
  DigestRow({
    required this.name,
    required this.dailyPrediction,
    required this.band,
    required this.isSpike,
  });

  final String name;
  final double dailyPrediction;

  /// Daily band: LOW / MEDIUM / HIGH.
  final String band;

  /// Today is >1.5x this camera's 7-day mean observed violations
  /// (see trend_analysis.isSpike).
  final bool isSpike;
}

/// Pure digest-body builder (unit-tested). Rows are in saved order;
/// only the first [kDigestMaxRows] are listed. The safest-departure
/// line and the estimates label are always present when the inputs
/// allow them.
String buildDigestText({
  required List<DigestRow> rows,
  required SafestDeparture? safest,
}) {
  final lines = <String>[];
  for (final row in rows.take(kDigestMaxRows)) {
    lines.add(
      '${row.name}: ${row.dailyPrediction.toStringAsFixed(1)}/day '
      '${row.band}${row.isSpike ? ' — SPIKE' : ''}',
    );
  }
  if (rows.length > kDigestMaxRows) {
    lines.add('…and ${rows.length - kDigestMaxRows} more saved cameras');
  }
  if (safest != null) {
    lines.add(
      'Safest to leave: ${safest.hour.toString().padLeft(2, '0')}:00 '
      '(estimates)',
    );
  }
  lines.add('Daily risk is an estimate.');
  return lines.join('\n');
}

/// The digest body when the user has no saved intersections: a
/// prompt, not an empty digest.
String buildNoSavedDigestText() =>
    'No saved intersections yet — save the cameras on your route and '
    'tomorrow’s digest will show their risk for the day.';

class MorningDigestService {
  MorningDigestService({NotificationGateway? notifications})
    : _notifications = notifications ?? LocalNotifications();

  final NotificationGateway _notifications;

  /// (Re)schedules the 07:00 digest when Pro is on and the digest
  /// toggle is on; cancels it otherwise. Fetches today's predictions
  /// for the saved cameras and builds the body, so the scheduled
  /// notification carries fresh data. Skips the fetch when today's
  /// digest is already scheduled, unless [force] is set (e.g. after
  /// a save/unsave changes the list). Failures are swallowed: a
  /// missed refresh just leaves yesterday's scheduled digest in
  /// place (or none), it never crashes the app or the background
  /// task.
  Future<void> ensureScheduled({
    required RedunatorApiClient client,
    required bool isPro,
    required bool enabled,
    DateTime? now,
    bool force = false,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final today = dateKey(now ?? DateTime.now());
    if (!isPro || !enabled) {
      await _cancel(prefs);
      return;
    }
    if (!force && prefs.getString(kDigestScheduledDateKey) == today) {
      return;
    }
    try {
      final saved = _readSaved(prefs);
      final built = saved.isEmpty
          ? (body: buildNoSavedDigestText(), hasSpike: false)
          : await _buildDigestBody(client, saved, now ?? DateTime.now());
      await _notifications.scheduleDailyDigest(
        title: 'Redunator morning digest',
        body: built.body,
        hour: kDigestHour,
        // A digest containing a spike sounds the spike alert
        // instead of the gentle morning one.
        hasSpike: built.hasSpike,
      );
      await prefs.setString(kDigestScheduledDateKey, today);
    } catch (_) {
      // Leave any previously scheduled digest alone; retry on the
      // next app start / background run.
    }
  }

  /// Builds the digest with live data and shows it immediately
  /// (Settings "Send test digest" button). Returns false when the
  /// predictions could not be fetched (e.g. offline).
  Future<bool> sendTestDigest({
    required RedunatorApiClient client,
    DateTime? now,
  }) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = _readSaved(prefs);
      final built = saved.isEmpty
          ? (body: buildNoSavedDigestText(), hasSpike: false)
          : await _buildDigestBody(client, saved, now ?? DateTime.now());
      await _notifications.showNow(
        title: 'Redunator morning digest (test)',
        body: built.body,
        hasSpike: built.hasSpike,
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  /// (Re)schedules the Sunday 07:00 weekly digest when Exclusive is
  /// on and the weekly toggle is on; cancels it otherwise. Same
  /// pattern as the daily digest: fetch once per week (the stored
  /// Sunday date guards it), build the body from the saved cameras'
  /// trends, and schedule the repeating weekly notification.
  Future<void> ensureWeeklyScheduled({
    required RedunatorApiClient client,
    required bool isExclusive,
    required bool enabled,
    DateTime? now,
    bool force = false,
  }) async {
    final prefs = await SharedPreferences.getInstance();
    final at = now ?? DateTime.now();
    if (!isExclusive || !enabled) {
      await _cancelWeekly(prefs);
      return;
    }
    final nextSunday = nextWeekday(at, kWeeklyDigestWeekday, kWeeklyDigestHour);
    final key = dateKey(nextSunday);
    if (!force && prefs.getString(kWeeklyDigestScheduledDateKey) == key) {
      return;
    }
    try {
      final saved = _readSaved(prefs);
      final body = saved.isEmpty
          ? 'No saved intersections yet — save the cameras on your '
                'route and the Sunday digest will review their week.'
          : await _buildWeeklyBody(client, saved);
      await _notifications.scheduleWeeklyDigest(
        title: 'Redunator weekly digest',
        body: body,
        weekday: kWeeklyDigestWeekday,
        hour: kWeeklyDigestHour,
      );
      await prefs.setString(kWeeklyDigestScheduledDateKey, key);
    } catch (_) {
      // Leave any previously scheduled weekly digest alone.
    }
  }

  /// Builds the weekly digest with live data and shows it
  /// immediately (Settings "Send test weekly digest" button).
  Future<bool> sendTestWeeklyDigest({
    required RedunatorApiClient client,
  }) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = _readSaved(prefs);
      final body = saved.isEmpty
          ? 'No saved intersections yet — save the cameras on your '
                'route and the Sunday digest will review their week.'
          : await _buildWeeklyBody(client, saved);
      await _notifications.showNow(
        title: 'Redunator weekly digest (test)',
        body: body,
        weekly: true,
      );
      return true;
    } catch (_) {
      return false;
    }
  }

  Future<void> _cancelWeekly(SharedPreferences prefs) async {
    try {
      await _notifications.cancelWeeklyDigest();
    } catch (_) {}
    await prefs.remove(kWeeklyDigestScheduledDateKey);
  }

  /// One trends fetch per saved camera, summarised into the weekly
  /// digest body (see weekly_report.dart).
  Future<String> _buildWeeklyBody(
    RedunatorApiClient client,
    List<SavedIntersection> saved,
  ) async {
    final summaries = <WeeklyCameraSummary>[];
    await Future.wait(
      saved.map((s) async {
        try {
          final trends = await client.getViolationTrends(
            cameraId: s.cameraId,
          );
          final summary = weeklySummary(
            name: s.intersection,
            days: trends.days,
          );
          if (summary != null) summaries.add(summary);
        } catch (_) {
          // One camera failing must not kill the digest.
        }
      }),
    );
    return buildWeeklyDigestText(summaries: summaries);
  }

  Future<void> _cancel(SharedPreferences prefs) async {
    try {
      await _notifications.cancelDigest();
    } catch (_) {
      // Cancelling is best-effort; the stored date is cleared
      // regardless so the next ensureScheduled re-schedules.
    }
    await prefs.remove(kDigestScheduledDateKey);
  }

  List<SavedIntersection> _readSaved(SharedPreferences prefs) {
    final raw = prefs.getString(SavedService.prefsKey);
    if (raw == null) return [];
    try {
      return (jsonDecode(raw) as List)
          .map((e) => SavedIntersection.fromJson(e as Map<String, dynamic>))
          .where((s) => s.cameraId.isNotEmpty)
          .toList();
    } catch (_) {
      return [];
    }
  }

  /// One batch prediction for all saved cameras, spike flags from
  /// each listed camera's 7-day trend (best-effort, skipped on
  /// failure), and the safest departure hour across the saved
  /// cameras' hourly curves (reuse of route_risk.dart). Returns the
  /// body plus whether any listed row is a spike — the scheduler
  /// uses that to pick the spike alert sound.
  Future<({String body, bool hasSpike})> _buildDigestBody(
    RedunatorApiClient client,
    List<SavedIntersection> saved,
    DateTime now,
  ) async {
    final batch = await client.predictRiskBatch(
      saved.map((s) => {'camera_id': s.cameraId}).toList(),
    );
    final preds = {for (final p in batch.results) p.cameraId: p};
    final rows = <DigestRow>[];
    for (final s in saved) {
      final p = preds[s.cameraId];
      if (p == null) continue;
      var spike = false;
      // Spike flags only matter for the rows actually shown.
      if (rows.length < kDigestMaxRows) {
        try {
          final trends = await client.getViolationTrends(
            cameraId: s.cameraId,
          );
          spike = isSpike(
            p.dailyPrediction,
            meanObservedWindow(trends.days, 7),
          );
        } catch (_) {
          // Best-effort: no spike flag rather than no digest.
        }
      }
      rows.add(
        DigestRow(
          name: s.intersection,
          dailyPrediction: p.dailyPrediction,
          band: p.riskLevel,
          isSpike: spike,
        ),
      );
    }
    final safest = safestDepartureHour(
      predictions: preds,
      cameraIds: saved.map((s) => s.cameraId).toList(),
      currentHour: kDigestHour,
    );
    return (
      body: buildDigestText(rows: rows, safest: safest),
      hasSpike: rows.any((r) => r.isSpike),
    );
  }
}
