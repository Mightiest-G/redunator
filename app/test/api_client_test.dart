// API client tests against a mock http.Client (tests only — the
// shipped app never uses mock data). Fixture values mirror the live
// backend smoke test in API_REFERENCE.md (camera 1413, 2026-10-15).
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/api/models.dart';

Map<String, dynamic> predictionJson({String cameraId = '1413'}) => {
      'camera_id': cameraId,
      'intersection': 'LAKE SHORE DR AND BELMONT',
      'latitude': 41.940241,
      'longitude': -87.639639,
      'date': '2026-10-15',
      'timestamp': '2026-10-15T12:00:00',
      'daily_prediction': 53.0095,
      'daily_source': 'model',
      'risk_score': 1.0,
      'risk_level': 'HIGH',
      'confidence': 0.9,
      'reasons': ['High historical volume at this camera.'],
      'hourly': List.generate(
        24,
        (h) => {
          'hour': h,
          'predicted_violations': h == 15 ? 4.8194 : 1.0,
          'band': h == 15 ? 'HIGH' : 'HIGH',
          'risk_score': h == 15 ? 0.1377 : 0.0286,
          'estimate': true,
        },
      ),
      'hourly_estimate_note': 'Hourly values are ESTIMATES.',
      'hourly_method': 'model',
      'hourly_sum': 53.0095,
      'reconciliation_error': 0.0,
      'model_version': 1,
      'nearest_distance_m': null,
      'weather_override_applied': false,
      'congestion_override_applied': false,
    };

void main() {
  group('RedunatorApiClient', () {
    test('predictRisk parses a full prediction', () async {
      var seenBody = <String, dynamic>{};
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          expect(req.url.path, '/predict_risk');
          seenBody = jsonDecode(req.body) as Map<String, dynamic>;
          return http.Response(jsonEncode(predictionJson()), 200);
        }),
      );
      final p = await client.predictRisk(
        cameraId: '1413',
        timestamp: DateTime(2026, 10, 15, 12),
      );
      // The date field is named `timestamp`, not `date`.
      expect(seenBody['timestamp'], contains('2026-10-15'));
      expect(seenBody.containsKey('date'), isFalse);
      expect(p.cameraId, '1413');
      expect(p.dailyPrediction, closeTo(53.0095, 0.0001));
      expect(p.riskScore, 1.0);
      expect(p.riskLevel, 'HIGH');
      expect(p.hourly.length, 24);
      expect(p.hourEntry(15)?.predictedViolations, closeTo(4.8194, 0.0001));
      expect(p.hourly.every((e) => e.estimate), isTrue);
    });

    test('predictRisk with lat/lon sends coordinates', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body['latitude'], 41.8781);
          expect(body['longitude'], -87.6298);
          return http.Response(jsonEncode(predictionJson(cameraId: '3041')), 200);
        }),
      );
      final p = await client.predictRisk(latitude: 41.8781, longitude: -87.6298);
      expect(p.cameraId, '3041');
    });

    test('predictRiskBatch returns results and counts per-item errors', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          return http.Response(
            jsonEncode({
              'results': [
                predictionJson(),
                {
                  'index': 1,
                  'camera_id': '9999',
                  'error': 'bad_request',
                  'message': 'nope',
                },
              ],
              'count': 2,
              'errors': 1,
            }),
            200,
          );
        }),
      );
      final batch = await client.predictRiskBatch([
        {'camera_id': '1413'},
        {'camera_id': '9999'},
      ]);
      expect(batch.count, 2);
      expect(batch.errors, 2); // 1 reported + 1 per-item error object
      expect(batch.results.length, 1);
      expect(batch.results.first.cameraId, '1413');
    });

    test('getIntersections parses the list', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          expect(req.url.queryParameters['limit'], '500');
          return http.Response(
            jsonEncode([
              {
                'camera_id': '1413',
                'intersection': 'LAKE SHORE DR AND BELMONT',
                'latitude': 41.940241,
                'longitude': -87.639639,
                'typical_weekday_pred': 60.54,
                'typical_weekend_pred': 74.38,
                'typical_weekday_band': 'HIGH',
                'typical_weekend_band': 'HIGH',
                'typical_weekday_score': 1.0,
                'typical_weekend_score': 1.0,
                'actual_test_mean': 55.0,
                'cam_mean_pre_test': 50.0,
              },
            ]),
            200,
          );
        }),
      );
      final list = await client.getIntersections(limit: 500);
      expect(list.length, 1);
      expect(list.first.intersection, 'LAKE SHORE DR AND BELMONT');
      expect(list.first.typicalWeekdayPred, 60.54);
    });

    test('getViolationTrends parses daily trends', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          return http.Response(
            jsonEncode({
              'camera_id': '1413',
              'intersection': 'LAKE SHORE DR AND BELMONT',
              'granularity': 'daily',
              'note': 'Daily only.',
              'start_date': '2026-10-13',
              'end_date': '2026-10-15',
              'days': [
                {
                  'date': '2026-10-13',
                  'observed_violations': 54,
                  'predicted_violations': 55.5,
                },
              ],
              'summary': {
                'n_days': 3,
                'mean_observed': 54.33,
                'mean_predicted': 55.5,
                'total_observed': 163,
              },
            }),
            200,
          );
        }),
      );
      final t = await client.getViolationTrends(cameraId: '1413');
      expect(t.granularity, 'daily');
      expect(t.nDays, 3);
      expect(t.meanObserved, closeTo(54.33, 0.01));
      expect(t.days.length, 1);
    });

    test('getLiveRisk parses live risk and never applies adjustment', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          expect(req.url.queryParameters['lat'], '41.8781');
          return http.Response(
            jsonEncode({
              'camera_id': '3041',
              'intersection': 'MICHIGAN AND JACKSON',
              'distance_m': 443,
              'latitude': 41.8781,
              'longitude': -87.6298,
              'at': '2026-10-15T05:00:00-05:00',
              'current_hour': 5,
              'date': '2026-10-15',
              'risk_score': 0.001,
              'risk_level': 'LOW',
              'predicted_violations_this_hour': 0.038,
              'daily_prediction': 10.3,
              'daily_band': 'HIGH',
              'daily_source': 'pre_test_mean',
              'confidence': 0.4,
              'reasons': ['Nearest camera 443 m away.'],
              'live_adjustment': {
                'adjustment_applied': false,
                'adjustment_factor': 1.0,
                'all_signals_stale': false,
              },
              'estimate': true,
              'hourly': List.filled(24, 0.43),
            }),
            200,
          );
        }),
      );
      final live = await client.getLiveRisk(
        latitude: 41.8781,
        longitude: -87.6298,
      );
      expect(live.cameraId, '3041');
      expect(live.adjustmentApplied, isFalse);
      expect(live.adjustmentFactor, 1.0);
      expect(live.estimate, isTrue);
      expect(live.hourly.length, 24);
    });

    test('reportSpike posts camera_id and returns the report', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          expect(req.url.path, '/user_report');
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body['camera_id'], '1413');
          return http.Response(
            jsonEncode({
              'id': 7,
              'created_at': '2026-11-16T12:00:00',
              'camera_id': '1413',
              'latitude': 41.940241,
              'longitude': -87.639639,
              'note': null,
            }),
            201,
          );
        }),
      );
      final report = await client.reportSpike(cameraId: '1413');
      expect(report.id, 7);
    });

    test('user alerts: list, create, delete', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          if (req.method == 'GET') {
            return http.Response(
              jsonEncode({
                'user_id': 'anonymous',
                'alerts': [
                  {
                    'id': 1,
                    'latitude': 41.94,
                    'longitude': -87.64,
                    'radius_m': 300,
                    'threshold': 'HIGH',
                    'threshold_value': null,
                    'name': 'Belmont',
                    'active': true,
                  },
                ],
              }),
              200,
            );
          }
          if (req.method == 'POST') {
            return http.Response(
              jsonEncode({
                'id': 2,
                'latitude': 41.94,
                'longitude': -87.64,
                'radius_m': 300,
                'threshold': 'HIGH',
                'threshold_value': null,
                'name': 'Belmont',
                'active': true,
              }),
              201,
            );
          }
          return http.Response(
            jsonEncode({
              'id': 1,
              'latitude': 41.94,
              'longitude': -87.64,
              'radius_m': 300,
              'threshold': 'HIGH',
              'threshold_value': null,
              'name': 'Belmont',
              'active': false,
            }),
            200,
          );
        }),
      );
      final alerts = await client.listUserAlerts();
      expect(alerts.length, 1);
      expect(alerts.first.active, isTrue);
      final created = await client.createUserAlert(
        latitude: 41.94,
        longitude: -87.64,
        radiusM: 300,
        threshold: 'HIGH',
        name: 'Belmont',
      );
      expect(created.id, 2);
      final deleted = await client.deleteUserAlert(1);
      expect(deleted.active, isFalse);
    });

    test('user settings: get and update', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          return http.Response(
            jsonEncode({
              'user_id': 'anonymous',
              'settings': {
                'units': 'metric',
                'alert_radius_m': 2000,
                'risk_threshold': 'MEDIUM',
                'notifications': {'email': false, 'push': false},
              },
            }),
            200,
          );
        }),
      );
      final settings = await client.getUserSettings();
      expect(settings.units, 'metric');
      expect(settings.alertRadiusM, 2000);
      final updated = await client.updateUserSettings({'units': 'imperial'});
      expect(updated.units, 'metric'); // mock echoes the same fixture
    });

    test('sendFeedback posts text', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          final body = jsonDecode(req.body) as Map<String, dynamic>;
          expect(body['text'], 'Great app');
          expect(body['rating'], 5);
          return http.Response(jsonEncode({'id': 1}), 201);
        }),
      );
      await client.sendFeedback(
        UserFeedback(text: 'Great app', rating: 5, category: 'app'),
      );
    });

    test('getHealth parses model version and freshness', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          return http.Response(
            jsonEncode({
              'status': 'ok',
              'model_version': 1,
              'model_files': {'joblib': 'redunator_brain_v1.joblib'},
              'model_metrics': {'mae': 2.145, 'rmse': 3.307},
              'data_freshness': {
                'latest_violation_date': '2026-10-29',
                'latest_violation_source': 'data/processed/red_light_features.parquet',
              },
            }),
            200,
          );
        }),
      );
      final h = await client.getHealth();
      expect(h.status, 'ok');
      expect(h.modelVersion, 1);
      expect(h.latestViolationDate, '2026-10-29');
    });

    test('backend error bodies surface as ApiException', () async {
      final client = RedunatorApiClient(
        baseUrl: 'http://10.0.2.2:8000',
        httpClient: MockClient((req) async {
          return http.Response(
            jsonEncode({
              'error': 'no_camera_nearby',
              'message': 'No camera within 2000 m.',
            }),
            404,
          );
        }),
      );
      expect(
        () => client.predictRisk(latitude: 0, longitude: 0),
        throwsA(
          isA<ApiException>()
              .having((e) => e.statusCode, 'status', 404)
              .having((e) => e.error, 'error', 'no_camera_nearby'),
        ),
      );
    });
  });
}
