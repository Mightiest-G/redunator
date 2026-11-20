// Unit tests for Pro background alerts: the pure HIGH-within-
// distance rule, the one-alert-per-camera-per-day dedupe, the
// Pro-active check, and the background-run orchestration (with
// fakes for the scheduler, notifications, location and HTTP).
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/background_alerts.dart';
import 'package:redunator/services/local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';

LiveRisk _liveRisk({
  required double distanceM,
  required String dailyBand,
  String cameraId = '1413',
}) => LiveRisk(
  cameraId: cameraId,
  intersection: 'LAKE SHORE DR AND BELMONT',
  distanceM: distanceM,
  latitude: 41.940241,
  longitude: -87.639639,
  at: '2026-10-15T12:00:00-05:00',
  currentHour: 12,
  date: '2026-10-15',
  riskScore: 0.1377,
  riskLevel: 'HIGH',
  predictedViolationsThisHour: 4.8194,
  dailyPrediction: 53.0095,
  dailyBand: dailyBand,
  dailySource: 'model',
  confidence: 0.9,
  reasons: const [],
  adjustmentApplied: false,
  adjustmentFactor: 1.0,
  allSignalsStale: false,
  estimate: true,
  hourly: List.filled(24, 1.0),
);

Map<String, dynamic> _liveRiskJson({
  required double distanceM,
  required String dailyBand,
}) => {
  'camera_id': '1413',
  'intersection': 'LAKE SHORE DR AND BELMONT',
  'distance_m': distanceM,
  'latitude': 41.940241,
  'longitude': -87.639639,
  'at': '2026-10-15T12:00:00-05:00',
  'current_hour': 12,
  'date': '2026-10-15',
  'risk_score': 0.1377,
  'risk_level': 'HIGH',
  'predicted_violations_this_hour': 4.8194,
  'daily_prediction': 53.0095,
  'daily_band': dailyBand,
  'daily_source': 'model',
  'confidence': 0.9,
  'reasons': [],
  'live_adjustment': {
    'adjustment_applied': false,
    'adjustment_factor': 1.0,
    'all_signals_stale': false,
  },
  'estimate': true,
  'hourly': List.filled(24, 1.0),
};

Position _pos(double lat, double lon) => Position(
  latitude: lat,
  longitude: lon,
  timestamp: DateTime.now(),
  accuracy: 5,
  altitude: 0,
  altitudeAccuracy: 0,
  heading: 0,
  headingAccuracy: 0,
  speed: 0,
  speedAccuracy: 0,
);

class _FakeScheduler implements BackgroundTaskScheduler {
  int registerCalls = 0;
  int cancelCalls = 0;

  @override
  Future<void> registerPeriodic() async {
    registerCalls++;
  }

  @override
  Future<void> cancel() async {
    cancelCalls++;
  }
}

class _FakeGateway implements NotificationGateway {
  final List<Map<String, Object?>> alerts = [];
  final List<Map<String, Object?>> scheduledDigests = [];
  final List<Map<String, Object?>> shownNow = [];
  int cancelDigestCalls = 0;

  @override
  Future<void> showBackgroundAlert({
    required String cameraId,
    required String intersection,
    required double distanceM,
    required String band,
    required double dailyPrediction,
  }) async {
    alerts.add({
      'cameraId': cameraId,
      'intersection': intersection,
      'distanceM': distanceM,
      'band': band,
      'dailyPrediction': dailyPrediction,
    });
  }

  @override
  Future<void> showNow({
    required String title,
    required String body,
  }) async {
    shownNow.add({'title': title, 'body': body});
  }

  @override
  Future<void> scheduleDailyDigest({
    required String title,
    required String body,
    required int hour,
  }) async {
    scheduledDigests.add({'title': title, 'body': body, 'hour': hour});
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
  }) async {
    alerts.add({'areaId': areaId, 'areaName': areaName, 'body': body});
  }

  @override
  Future<void> scheduleWeeklyDigest({
    required String title,
    required String body,
    required int weekday,
    required int hour,
  }) async {
    scheduledDigests.add({
      'title': title,
      'body': body,
      'weekday': weekday,
      'hour': hour,
    });
  }

  @override
  Future<void> cancelWeeklyDigest() async {
    cancelDigestCalls++;
  }

  @override
  Future<bool> requestNotificationPermission() async => true;

  @override
  Future<bool> canScheduleExactAlarms() async => false;

  @override
  Future<void> requestExactAlarmsPermission() async {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('backgroundAlertTarget', () {
    test('HIGH within distance alerts', () {
      final target = backgroundAlertTarget(
        _liveRisk(distanceM: 120, dailyBand: 'HIGH'),
        alertDistanceM: 250,
      );
      expect(target, isNotNull);
      expect(target!.cameraId, '1413');
    });

    test('exactly at the alert distance alerts', () {
      expect(
        backgroundAlertTarget(
          _liveRisk(distanceM: 250, dailyBand: 'HIGH'),
          alertDistanceM: 250,
        ),
        isNotNull,
      );
    });

    test('HIGH beyond the distance does not alert', () {
      expect(
        backgroundAlertTarget(
          _liveRisk(distanceM: 251, dailyBand: 'HIGH'),
          alertDistanceM: 250,
        ),
        isNull,
      );
    });

    test('MEDIUM and LOW bands never alert', () {
      expect(
        backgroundAlertTarget(
          _liveRisk(distanceM: 50, dailyBand: 'MEDIUM'),
          alertDistanceM: 250,
        ),
        isNull,
      );
      expect(
        backgroundAlertTarget(
          _liveRisk(distanceM: 50, dailyBand: 'LOW'),
          alertDistanceM: 250,
        ),
        isNull,
      );
    });
  });

  group('DailyAlertDedupe', () {
    test('one alert per camera per day, again the next day', () async {
      final prefs = await SharedPreferences.getInstance();
      final dedupe = DailyAlertDedupe(prefs);
      final today = DateTime(2026, 11, 19, 10, 30);
      expect(dedupe.shouldAlert('1413', today), isTrue);
      await dedupe.recordAlert('1413', today);
      expect(dedupe.shouldAlert('1413', today), isFalse);
      // A different camera is unaffected.
      expect(dedupe.shouldAlert('2251', today), isTrue);
      // Tomorrow it alerts again.
      expect(
        dedupe.shouldAlert('1413', DateTime(2026, 11, 20, 8, 0)),
        isTrue,
      );
    });
  });

  group('isProActive', () {
    test('false without the flag, true when set', () async {
      var prefs = await SharedPreferences.getInstance();
      expect(BackgroundAlertsService.isProActive(prefs), isFalse);
      SharedPreferences.setMockInitialValues({'is_pro': true});
      prefs = await SharedPreferences.getInstance();
      expect(BackgroundAlertsService.isProActive(prefs), isTrue);
    });

    test('false when the cached Pro subscription expired', () async {
      SharedPreferences.setMockInitialValues({
        'is_pro': true,
        'pro_expiry_ms':
            DateTime.now().millisecondsSinceEpoch - 1000,
      });
      final prefs = await SharedPreferences.getInstance();
      expect(BackgroundAlertsService.isProActive(prefs), isFalse);
    });

    test('true when the expiry is in the future', () async {
      SharedPreferences.setMockInitialValues({
        'is_pro': true,
        'pro_expiry_ms':
            DateTime.now().millisecondsSinceEpoch + 86400000,
      });
      final prefs = await SharedPreferences.getInstance();
      expect(BackgroundAlertsService.isProActive(prefs), isTrue);
    });
  });

  group('syncWithPro', () {
    test('registers when Pro and enabled, cancels otherwise', () async {
      final scheduler = _FakeScheduler();
      final svc = BackgroundAlertsService(
        scheduler: scheduler,
        notifications: _FakeGateway(),
      );
      await svc.syncWithPro(isPro: true, enabled: true);
      expect(scheduler.registerCalls, 1);
      expect(scheduler.cancelCalls, 0);
      await svc.syncWithPro(isPro: true, enabled: false);
      expect(scheduler.cancelCalls, 1);
      await svc.syncWithPro(isPro: false, enabled: true);
      expect(scheduler.cancelCalls, 2);
      expect(scheduler.registerCalls, 1);
    });
  });

  group('runBackgroundCheck', () {
    test('non-Pro run does nothing: no location, no HTTP, no alert',
        () async {
      SharedPreferences.setMockInitialValues({'is_pro': false});
      var positionCalls = 0;
      var httpCalls = 0;
      final gateway = _FakeGateway();
      final svc = BackgroundAlertsService(
        scheduler: _FakeScheduler(),
        notifications: gateway,
        positionOverride: () async {
          positionCalls++;
          return _pos(41.94, -87.64);
        },
        clientFactory: ({required String baseUrl}) => RedunatorApiClient(
          baseUrl: baseUrl,
          httpClient: MockClient((req) async {
            httpCalls++;
            return http.Response('{}', 200);
          }),
        ),
      );
      await svc.runBackgroundCheck();
      expect(positionCalls, 0);
      expect(httpCalls, 0);
      expect(gateway.alerts, isEmpty);
    });

    test('Pro run alerts once per camera per day', () async {
      SharedPreferences.setMockInitialValues({
        'is_pro': true,
        'pro_alert_distance_m': 250.0,
      });
      final gateway = _FakeGateway();
      final svc = BackgroundAlertsService(
        scheduler: _FakeScheduler(),
        notifications: gateway,
        positionOverride: () async => _pos(41.94, -87.64),
        clientFactory: ({required String baseUrl}) => RedunatorApiClient(
          baseUrl: baseUrl,
          httpClient: MockClient((req) async {
            if (req.url.path == '/live/risk') {
              return http.Response(
                jsonEncode(
                  _liveRiskJson(distanceM: 120, dailyBand: 'HIGH'),
                ),
                200,
              );
            }
            return http.Response('not found', 404);
          }),
        ),
      );
      await svc.runBackgroundCheck();
      expect(gateway.alerts.length, 1);
      expect(gateway.alerts.first['cameraId'], '1413');
      expect(gateway.alerts.first['band'], 'HIGH');
      // A second run the same day does not alert again.
      await svc.runBackgroundCheck();
      expect(gateway.alerts.length, 1);
    });

    test('toggle off means no check at all', () async {
      SharedPreferences.setMockInitialValues({
        'is_pro': true,
        'background_alerts_enabled': false,
      });
      var positionCalls = 0;
      final gateway = _FakeGateway();
      final svc = BackgroundAlertsService(
        scheduler: _FakeScheduler(),
        notifications: gateway,
        positionOverride: () async {
          positionCalls++;
          return _pos(41.94, -87.64);
        },
        clientFactory: ({required String baseUrl}) => RedunatorApiClient(
          baseUrl: baseUrl,
          httpClient: MockClient(
            (req) async => http.Response('{}', 200),
          ),
        ),
      );
      await svc.runBackgroundCheck();
      expect(positionCalls, 0);
      expect(gateway.alerts, isEmpty);
    });

    test('no camera nearby (404) is not an error and alerts nothing',
        () async {
      SharedPreferences.setMockInitialValues({'is_pro': true});
      final gateway = _FakeGateway();
      final svc = BackgroundAlertsService(
        scheduler: _FakeScheduler(),
        notifications: gateway,
        positionOverride: () async => _pos(41.94, -87.64),
        clientFactory: ({required String baseUrl}) => RedunatorApiClient(
          baseUrl: baseUrl,
          httpClient: MockClient((req) async {
            if (req.url.path == '/live/risk') {
              return http.Response(
                jsonEncode({
                  'error': 'no_camera_nearby',
                  'message': 'No camera within 250 m',
                }),
                404,
              );
            }
            return http.Response('not found', 404);
          }),
        ),
      );
      await svc.runBackgroundCheck();
      expect(gateway.alerts, isEmpty);
    });

    test('MEDIUM nearest camera does not alert', () async {
      SharedPreferences.setMockInitialValues({'is_pro': true});
      final gateway = _FakeGateway();
      final svc = BackgroundAlertsService(
        scheduler: _FakeScheduler(),
        notifications: gateway,
        positionOverride: () async => _pos(41.94, -87.64),
        clientFactory: ({required String baseUrl}) => RedunatorApiClient(
          baseUrl: baseUrl,
          httpClient: MockClient((req) async {
            if (req.url.path == '/live/risk') {
              return http.Response(
                jsonEncode(
                  _liveRiskJson(distanceM: 120, dailyBand: 'MEDIUM'),
                ),
                200,
              );
            }
            return http.Response('not found', 404);
          }),
        ),
      );
      await svc.runBackgroundCheck();
      expect(gateway.alerts, isEmpty);
    });
  });
}
