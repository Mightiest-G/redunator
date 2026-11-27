// Unit tests for the Pro morning digest: the pure digest-text
// builder (rows, spike flags, safest hour, row cap, estimates
// label) and the scheduling service (Pro gate, no-saved prompt,
// once-a-day scheduling, test send).
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/services/local_notifications.dart';
import 'package:redunator/services/morning_digest.dart';
import 'package:redunator/services/route_risk.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeGateway implements NotificationGateway {
  final List<Map<String, Object?>> scheduledDigests = [];
  final List<Map<String, Object?>> shownNow = [];
  int cancelDigestCalls = 0;
  bool throwOnSchedule = false;
  List<PendingNotificationSummary> pendingDigests = [];

  @override
  Future<void> showBackgroundAlert({
    required String cameraId,
    required String intersection,
    required double distanceM,
    required String band,
    required double dailyPrediction,
  }) async {}

  @override
  Future<void> showNow({
    required String title,
    required String body,
    bool hasSpike = false,
    bool weekly = false,
  }) async {
    shownNow.add({
      'title': title,
      'body': body,
      'hasSpike': hasSpike,
      'weekly': weekly,
    });
  }

  @override
  Future<void> scheduleDailyDigest({
    required String title,
    required String body,
    required int hour,
    bool hasSpike = false,
  }) async {
    if (throwOnSchedule) throw StateError('schedule boom');
    scheduledDigests.add({
      'title': title,
      'body': body,
      'hour': hour,
      'hasSpike': hasSpike,
    });
  }

  @override
  Future<void> cancelDigest() async {
    cancelDigestCalls++;
  }

  @override
  Future<void> showAreaAlert({
    required String areaId,
    required String areaName,
    required String body,
  }) async {}

  @override
  Future<void> scheduleWeeklyDigest({
    required String title,
    required String body,
    required int weekday,
    required int hour,
  }) async {}

  @override
  Future<void> cancelWeeklyDigest() async {}

  @override
  Future<bool> requestNotificationPermission() async => true;

  @override
  Future<bool> canScheduleExactAlarms() async => false;

  @override
  Future<List<PendingNotificationSummary>> pendingDigestNotifications() async =>
      pendingDigests;

  @override
  Future<void> requestExactAlarmsPermission() async {}
}

Map<String, dynamic> _predictionJson({
  required String cameraId,
  required String name,
  required double daily,
  required String band,
}) => {
  'camera_id': cameraId,
  'intersection': name,
  'latitude': 41.940241,
  'longitude': -87.639639,
  'date': '2026-10-15',
  'timestamp': '2026-10-15T12:00:00',
  'daily_prediction': daily,
  'daily_source': 'model',
  'risk_score': daily / 35,
  'risk_level': band,
  'confidence': 0.9,
  'reasons': [],
  // Hour 10 is the cheapest hour (0.5); 15:00 is the peak (4.8).
  'hourly': List.generate(
    24,
    (h) => {
      'hour': h,
      'predicted_violations': h == 10
          ? 0.5
          : h == 15
          ? 4.8
          : 1.0,
      'band': 'HIGH',
      'risk_score': 0.03,
      'estimate': true,
    },
  ),
  'hourly_estimate_note': 'Hourly values are ESTIMATES.',
  'hourly_method': 'model',
  'hourly_sum': daily,
  'reconciliation_error': 0.0,
  'model_version': 1,
  'nearest_distance_m': null,
};

Map<String, dynamic> _trendsJson() {
  // 14 days at 2.0/day observed: 7-day mean 2.0, so 53.0/day is a
  // spike (>1.5x) and 3.0/day is not (3.0 is not > 3.0).
  final days = List.generate(
    14,
    (i) => {
      'date': '2026-10-${(i + 1).toString().padLeft(2, '0')}',
      'observed_violations': 2,
      'predicted_violations': 2.0,
    },
  );
  return {
    'camera_id': '1413',
    'intersection': 'LAKE SHORE DR AND BELMONT',
    'granularity': 'daily',
    'note': '',
    'start_date': '2026-10-01',
    'end_date': '2026-10-14',
    'days': days,
    'summary': {
      'n_days': 14,
      'mean_observed': 2.0,
      'mean_predicted': 2.0,
      'total_observed': 28,
    },
  };
}

String _savedJson() => jsonEncode([
  {
    'camera_id': '1413',
    'intersection': 'LAKE SHORE DR AND BELMONT',
    'latitude': 41.940241,
    'longitude': -87.639639,
    'server_alert_id': null,
  },
  {
    'camera_id': '2251',
    'intersection': 'CICERO AND I-55',
    'latitude': 41.82,
    'longitude': -87.72,
    'server_alert_id': null,
  },
]);

RedunatorApiClient _digestClient({double daily1413 = 53.0}) =>
    RedunatorApiClient(
      baseUrl: 'https://example.invalid',
      httpClient: MockClient((req) async {
        if (req.url.path == '/predict_risk/batch') {
          return http.Response(
            jsonEncode({
              'results': [
                _predictionJson(
                  cameraId: '1413',
                  name: 'LAKE SHORE DR AND BELMONT',
                  daily: daily1413,
                  band: 'HIGH',
                ),
                _predictionJson(
                  cameraId: '2251',
                  name: 'CICERO AND I-55',
                  daily: 3.0,
                  band: 'MEDIUM',
                ),
              ],
              'count': 2,
              'errors': 0,
            }),
            200,
          );
        }
        if (req.url.path == '/violation_trends') {
          return http.Response(jsonEncode(_trendsJson()), 200);
        }
        return http.Response('not found', 404);
      }),
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  test('dateKey formats a device-local calendar date', () {
    expect(dateKey(DateTime(2026, 11, 5, 23, 59)), '2026-11-05');
    expect(dateKey(DateTime(2026, 1, 9)), '2026-01-09');
  });

  group('buildDigestText', () {
    test('rows, spike flag, safest hour and estimates label', () {
      final text = buildDigestText(
        rows: [
          DigestRow(
            name: 'LAKE SHORE DR AND BELMONT',
            dailyPrediction: 53.0,
            band: 'HIGH',
            isSpike: true,
          ),
          DigestRow(
            name: 'CICERO AND I-55',
            dailyPrediction: 3.0,
            band: 'MEDIUM',
            isSpike: false,
          ),
        ],
        safest: SafestDeparture(
          hour: 10,
          nowTotal: 4.0,
          safestTotal: 1.0,
          cameraCount: 2,
        ),
      );
      expect(
        text,
        contains('LAKE SHORE DR AND BELMONT: 53.0/day HIGH — SPIKE'),
      );
      expect(text, contains('CICERO AND I-55: 3.0/day MEDIUM'));
      expect(text, contains('Safest to leave: 10:00 (estimates)'));
      expect(text, contains('Daily risk is an estimate.'));
    });

    test('more than three rows collapse into an "and N more" line', () {
      final text = buildDigestText(
        rows: List.generate(
          5,
          (i) => DigestRow(
            name: 'CAM $i',
            dailyPrediction: 1.0,
            band: 'LOW',
            isSpike: false,
          ),
        ),
        safest: null,
      );
      expect(text, contains('CAM 0'));
      expect(text, contains('CAM 2'));
      expect(text, isNot(contains('CAM 3')));
      expect(text, contains('…and 2 more saved cameras'));
      expect(text, isNot(contains('Safest to leave')));
    });

    test('empty rows give just the estimates label', () {
      expect(
        buildDigestText(rows: [], safest: null),
        'Daily risk is an estimate.',
      );
    });
  });

  test('no-saved digest prompts instead of sending an empty digest', () {
    final text = buildNoSavedDigestText();
    expect(text, contains('No saved intersections yet'));
    expect(text, contains('save the cameras'));
  });

  group('ensureScheduled', () {
    test('schedules the digest with saved cameras, once per day', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      final now = DateTime(2026, 11, 19, 8, 0);
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: now,
      );
      expect(gateway.scheduledDigests.length, 1);
      final first = gateway.scheduledDigests.first;
      expect(first['title'], 'Redunator morning digest');
      expect(first['hour'], 7);
      // 53.0/day vs a 2.0 7-day mean is a spike, so the digest is
      // routed to the spike channel (its own sound).
      expect(first['hasSpike'], isTrue);
      final body = first['body'] as String;
      expect(body, contains('LAKE SHORE DR AND BELMONT: 53.0/day HIGH'));
      expect(body, contains('SPIKE'));
      expect(body, contains('CICERO AND I-55: 3.0/day MEDIUM'));
      expect(body, contains('Safest to leave: 10:00 (estimates)'));
      expect(body, contains('Daily risk is an estimate.'));
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(kDigestScheduledDateKey), '2026-11-19');
      // A second call the same day does not re-schedule.
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: now,
      );
      expect(gateway.scheduledDigests.length, 1);
      // Forcing re-schedules (e.g. after a save/unsave).
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: now,
        force: true,
      );
      expect(gateway.scheduledDigests.length, 2);
    });

    test('no spike in the digest routes to the gentle sound', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      await svc.ensureScheduled(
        client: _digestClient(daily1413: 3.0),
        isPro: true,
        enabled: true,
        now: DateTime(2026, 11, 19, 8, 0),
      );
      expect(gateway.scheduledDigests.length, 1);
      expect(gateway.scheduledDigests.first['hasSpike'], isFalse);
      expect(
        gateway.scheduledDigests.first['body'] as String,
        isNot(contains('SPIKE')),
      );
    });

    test('no saved intersections schedules the prompt text', () async {
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: DateTime(2026, 11, 19, 8, 0),
      );
      expect(gateway.scheduledDigests.length, 1);
      expect(
        gateway.scheduledDigests.first['body'] as String,
        contains('No saved intersections yet'),
      );
    });

    test('schedules a fallback digest when the fetch fails', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient((req) async => http.Response('boom', 500)),
      );
      await svc.ensureScheduled(
        client: client,
        isPro: true,
        enabled: true,
        now: DateTime(2026, 11, 19, 8, 0),
      );
      // Tomorrow still gets a digest — an honest one, not silence.
      expect(gateway.scheduledDigests.length, 1);
      expect(gateway.scheduledDigests.first['hour'], 7);
      expect(gateway.scheduledDigests.first['hasSpike'], isFalse);
      expect(
        gateway.scheduledDigests.first['body'] as String,
        contains('Couldn’t fetch today’s risk'),
      );
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(kDigestScheduledDateKey), '2026-11-19');
    });

    test(
      'non-Pro or disabled cancels the digest and never schedules',
      () async {
        SharedPreferences.setMockInitialValues({
          'saved_intersections': _savedJson(),
          kDigestScheduledDateKey: '2026-11-19',
        });
        final gateway = _FakeGateway();
        final svc = MorningDigestService(notifications: gateway);
        await svc.ensureScheduled(
          client: _digestClient(),
          isPro: false,
          enabled: true,
          now: DateTime(2026, 11, 19, 8, 0),
        );
        expect(gateway.scheduledDigests, isEmpty);
        expect(gateway.cancelDigestCalls, 1);
        final prefs = await SharedPreferences.getInstance();
        expect(prefs.getString(kDigestScheduledDateKey), isNull);

        await svc.ensureScheduled(
          client: _digestClient(),
          isPro: true,
          enabled: false,
          now: DateTime(2026, 11, 19, 8, 0),
        );
        expect(gateway.scheduledDigests, isEmpty);
        expect(gateway.cancelDigestCalls, 2);
      },
    );
  });

  group('schedule attempt recording', () {
    test('scheduled attempt records outcome, exact flag and next 07:00', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: DateTime(2026, 11, 19, 8, 0),
      );
      final attempt = await svc.lastScheduleAttempt();
      expect(attempt, isNotNull);
      expect(attempt!.outcome, 'scheduled');
      // The fake gateway reports exact alarms as unavailable.
      expect(attempt.exact, isFalse);
      // 08:00 is past 07:00, so the digest is scheduled for tomorrow.
      expect(attempt.scheduledFor, DateTime(2026, 11, 20, 7, 0));
      expect(attempt.error, isNull);
      expect(await svc.lastScheduledDate(), '2026-11-19');
    });

    test('second call the same day records a skip', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      final now = DateTime(2026, 11, 19, 8, 0);
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: now,
      );
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: now,
      );
      expect(gateway.scheduledDigests.length, 1);
      final attempt = await svc.lastScheduleAttempt();
      expect(attempt, isNotNull);
      expect(attempt!.outcome, 'skipped');
      expect(attempt.exact, isNull);
      expect(attempt.scheduledFor, isNull);
    });

    test('a schedule failure is recorded with the error', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway()..throwOnSchedule = true;
      final svc = MorningDigestService(notifications: gateway);
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: DateTime(2026, 11, 19, 8, 0),
      );
      expect(gateway.scheduledDigests, isEmpty);
      final attempt = await svc.lastScheduleAttempt();
      expect(attempt, isNotNull);
      expect(attempt!.outcome, 'failed');
      expect(attempt.error, contains('schedule boom'));
      expect(attempt.scheduledFor, DateTime(2026, 11, 20, 7, 0));
      // A failed schedule must not mark the day as scheduled.
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(kDigestScheduledDateKey), isNull);
    });

    test('cancelled attempt is recorded when not Pro', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
        kDigestScheduledDateKey: '2026-11-19',
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: false,
        enabled: true,
        now: DateTime(2026, 11, 19, 8, 0),
      );
      expect(gateway.cancelDigestCalls, 1);
      final attempt = await svc.lastScheduleAttempt();
      expect(attempt, isNotNull);
      expect(attempt!.outcome, 'cancelled');
    });

    test('diagnostics readers surface pending, exact state and stored date',
        () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway()
        ..pendingDigests = [
          const PendingNotificationSummary(
            id: 1001,
            title: 'Redunator morning digest',
            body: 'LAKE SHORE DR AND BELMONT: 53.0/day HIGH',
          ),
        ];
      final svc = MorningDigestService(notifications: gateway);
      final pending = await svc.pendingDigestNotifications();
      expect(pending.length, 1);
      expect(pending.first.id, 1001);
      expect(await svc.exactAlarmsAllowed(), isFalse);
      expect(await svc.lastScheduledDate(), isNull);
      await svc.ensureScheduled(
        client: _digestClient(),
        isPro: true,
        enabled: true,
        now: DateTime(2026, 11, 19, 8, 0),
      );
      expect(await svc.lastScheduledDate(), '2026-11-19');
    });
  });

  group('DigestScheduleAttempt JSON', () {
    test('round-trips through JSON', () {
      final original = DigestScheduleAttempt(
        attemptedAt: DateTime(2026, 11, 27, 15, 22),
        outcome: 'failed',
        exact: false,
        scheduledFor: DateTime(2026, 11, 28, 7, 0),
        error: 'Boom',
      );
      final decoded = DigestScheduleAttempt.fromJson(
        jsonEncode(original.toJson()),
      );
      expect(decoded, isNotNull);
      expect(decoded!.attemptedAt, original.attemptedAt);
      expect(decoded.outcome, 'failed');
      expect(decoded.exact, isFalse);
      expect(decoded.scheduledFor, DateTime(2026, 11, 28, 7, 0));
      expect(decoded.error, 'Boom');
    });

    test('malformed JSON returns null instead of throwing', () {
      expect(DigestScheduleAttempt.fromJson('not json'), isNull);
      expect(DigestScheduleAttempt.fromJson('{"outcome": 1}'), isNull);
      expect(DigestScheduleAttempt.fromJson('{}'), isNull);
    });
  });

  group('sendTestDigest', () {
    test('shows the digest immediately', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      final ok = await svc.sendTestDigest(
        client: _digestClient(),
        now: DateTime(2026, 11, 19, 8, 0),
      );
      expect(ok, isTrue);
      expect(gateway.shownNow.length, 1);
      expect(
        gateway.shownNow.first['title'],
        'Redunator morning digest (test)',
      );
      // The test digest carries the same spike routing as the
      // scheduled one.
      expect(gateway.shownNow.first['hasSpike'], isTrue);
      expect(gateway.shownNow.first['weekly'], isFalse);
      expect(
        gateway.shownNow.first['body'] as String,
        contains('LAKE SHORE DR AND BELMONT'),
      );
    });

    test('returns false when the backend is unreachable', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      final client = RedunatorApiClient(
        baseUrl: 'https://example.invalid',
        httpClient: MockClient((req) async => http.Response('boom', 500)),
      );
      final ok = await svc.sendTestDigest(client: client);
      expect(ok, isFalse);
      expect(gateway.shownNow, isEmpty);
    });
  });

  group('sendTestWeeklyDigest', () {
    test('shows the weekly digest on the weekly channel', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': _savedJson(),
      });
      final gateway = _FakeGateway();
      final svc = MorningDigestService(notifications: gateway);
      final ok = await svc.sendTestWeeklyDigest(client: _digestClient());
      expect(ok, isTrue);
      expect(gateway.shownNow.length, 1);
      expect(gateway.shownNow.first['title'], 'Redunator weekly digest (test)');
      expect(gateway.shownNow.first['weekly'], isTrue);
      expect(gateway.shownNow.first['hasSpike'], isFalse);
    });
  });
}
