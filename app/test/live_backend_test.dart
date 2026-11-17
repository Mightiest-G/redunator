// LIVE-backend integration test — Worker 3, 2026-11-16.
//
// Runs the REAL RedunatorApiClient (no mock http.Client) against a
// REAL backend. Start the backend first:
//
//   ~/workspace/redunator-venv/bin/python -m backend.app   # from ~/workspace/redunator/
//
// Base URL: $REDUNATOR_LIVE_BASE_URL, default http://127.0.0.1:8000.
// If the backend is not reachable, every test SKIPS (so plain
// `flutter test` without a backend still passes 27/27).
//
// Writes to the backend's dev SQLite DB: one spike report, one alert
// (created then deactivated), one settings round-trip (same defaults),
// one feedback row — all under user id "w3-live-test" and marked as a
// verification test in their note/text, so they are identifiable.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/api/models.dart';

void main() {
  final baseUrl = Platform.environment['REDUNATOR_LIVE_BASE_URL'] ??
      'http://127.0.0.1:8000';
  late final RedunatorApiClient client;
  var live = false;

  setUpAll(() async {
    client = RedunatorApiClient(
      baseUrl: baseUrl,
      userId: 'w3-live-test',
    );
    try {
      final h = await client
          .getHealth()
          .timeout(const Duration(seconds: 10));
      live = h.status == 'ok';
    } catch (_) {
      live = false;
    }
  });

  tearDownAll(() => client.close());

  void requireLive() {
    if (!live) {
      markTestSkipped(
        'Live backend not reachable at $baseUrl — start it with '
        '`~/workspace/redunator-venv/bin/python -m backend.app`.',
      );
    }
  }

  test('live: /health reports model v1, data through 2026-10-29', () async {
    requireLive();
    final h = await client.getHealth();
    expect(h.status, 'ok');
    expect(h.modelVersion, 1);
    expect(h.latestViolationDate, '2026-10-29');
    expect((h.modelMetrics['mae'] as num).toDouble(), closeTo(2.145, 0.01));
    expect((h.modelMetrics['rmse'] as num).toDouble(), closeTo(3.307, 0.01));
  });

  test('live: /predict_risk cam 1413 @ 2026-10-15 (map detail sheet data)',
      () async {
    requireLive();
    final p = await client.predictRisk(
      cameraId: '1413',
      timestamp: DateTime.utc(2026, 10, 15, 12),
    );
    expect(p.cameraId, '1413');
    expect(p.intersection, contains('BELMONT'));
    expect(p.dailySource, 'model');
    expect(p.dailyPrediction, closeTo(53.0095, 0.01));
    expect(p.riskLevel, 'HIGH');
    expect(p.riskScore, 1.0);
    expect(p.confidence, closeTo(0.9, 0.01));
    expect(p.hourly.length, 24);
    expect(p.hourly.every((e) => e.estimate), isTrue);
    expect(p.hourlySum, closeTo(p.dailyPrediction, 0.001));
    expect(p.reconciliationError, 0.0);
    final peak = p.hourEntry(15)!;
    expect(peak.predictedViolations, closeTo(4.8194, 0.01));
    expect(peak.band, 'HIGH');
    expect(p.reasons, isNotEmpty);
  });

  test('live: /predict_risk/batch (map markers, chunk of 2)', () async {
    requireLive();
    final b = await client.predictRiskBatch([
      {'camera_id': '1413', 'timestamp': '2026-10-15T12:00:00'},
      {'camera_id': '3041'},
    ]);
    expect(b.count, 2);
    expect(b.errors, 0);
    expect(b.results[0].dailyPrediction, closeTo(53.0095, 0.01));
    expect(b.results[1].dailyPrediction, closeTo(10.2965, 0.01));
  });

  test('live: /intersections returns the 300 profiled cameras', () async {
    requireLive();
    final list = await client.getIntersections(limit: 500);
    expect(list.length, 300);
    final first = list.firstWhere((c) => c.cameraId == '1413');
    expect(first.intersection, 'LAKE SHORE DR AND BELMONT');
    expect(first.typicalWeekdayPred, closeTo(60.54, 0.05));
    expect(first.typicalWeekdayBand, 'HIGH');
    expect(list.every((c) => c.latitude != null), isTrue);
  });

  test('live: /violation_trends cam 1413, 3 days (detail sheet)', () async {
    requireLive();
    final t = await client.getViolationTrends(
      cameraId: '1413',
      startDate: DateTime.utc(2026, 10, 13),
      endDate: DateTime.utc(2026, 10, 15),
    );
    expect(t.granularity, 'daily');
    expect(t.days.length, 3);
    expect(t.nDays, 3);
    expect(t.totalObserved, closeTo(163, 0.5));
    expect(t.meanObserved, closeTo(54.33, 0.05));
  });

  test('live: /live/risk downtown — no live adjustment ships', () async {
    requireLive();
    final r = await client.getLiveRisk(
      latitude: 41.8781,
      longitude: -87.6298,
    );
    expect(r.cameraId, '3041');
    expect(r.intersection, contains('MICHIGAN'));
    expect(r.distanceM, closeTo(443, 5));
    expect(r.adjustmentApplied, isFalse);
    expect(r.adjustmentFactor, 1.0);
    expect(r.estimate, isTrue);
    expect(r.hourly.length, 24);
    // NOTE: /live/risk rounds daily to 2 dp (live.py:204) and each hourly
    // value to 3 dp (live.py:118) without renormalising, so the hourly sum
    // (10.296) differs from daily_prediction (10.3) by 0.004 for cam 3041.
    // /predict_risk reconciles exactly; this is a display-rounding
    // artifact only (0.04%, invisible at display precision, bands
    // unaffected). Reported to parent; backend deliberately not touched.
    expect(
      r.hourly.fold<double>(0, (a, b) => a + b),
      closeTo(r.dailyPrediction, 0.01),
    );
    expect(r.dailySource, 'pre_test_mean');
  });

  test('live: Report Spike round-trip (POST then GET)', () async {
    requireLive();
    final created = await client.reportSpike(
      cameraId: '1413',
      note: '[Worker 3 live verification 2026-11-16] test spike report',
    );
    expect(created.id, greaterThan(0));
    expect(created.cameraId, '1413');
    final fetched = await client.getUserReport(created.id);
    expect(fetched.id, created.id);
    expect(fetched.note, contains('Worker 3 live verification'));
  });

  test('live: user alerts create → list → deactivate', () async {
    requireLive();
    final created = await client.createUserAlert(
      latitude: 41.940241,
      longitude: -87.639639,
      radiusM: 300,
      threshold: 'HIGH',
      name: '[Worker 3 live verification 2026-11-16] test alert',
    );
    expect(created.id, greaterThan(0));
    expect(created.active, isTrue);
    final listed = await client.listUserAlerts();
    expect(listed.any((a) => a.id == created.id), isTrue);
    final deactivated = await client.deleteUserAlert(created.id);
    expect(deactivated.active, isFalse);
  });

  test('live: user settings round-trip (defaults)', () async {
    requireLive();
    final before = await client.getUserSettings();
    expect(before.units, 'metric');
    expect(before.alertRadiusM, 2000);
    expect(before.riskThreshold, 'MEDIUM');
    final after = await client.updateUserSettings({
      'units': 'metric',
      'alert_radius_m': 2000,
      'risk_threshold': 'MEDIUM',
    });
    expect(after.units, 'metric');
    expect(after.alertRadiusM, 2000);
    expect(after.riskThreshold, 'MEDIUM');
  });

  test('live: feedback posts (201)', () async {
    requireLive();
    final resp = await client.sendFeedback(UserFeedback(
      text: '[Worker 3 live verification 2026-11-16] test feedback row',
      rating: 5,
      category: 'verification',
    ));
    expect(resp['id'], isNotNull);
  });

  test('live: error paths surface typed ApiExceptions', () async {
    requireLive();
    // No camera_id and no lat/lon -> 400 bad_request.
    expect(
      () => client.predictRisk(),
      throwsA(isA<ApiException>().having(
        (e) => e.statusCode,
        'statusCode',
        400,
      )),
    );
    // Mid-ocean: no camera within 2000 m -> 404 no_camera_nearby.
    expect(
      () => client.getLiveRisk(latitude: 0, longitude: 0),
      throwsA(isA<ApiException>().having(
        (e) => e.statusCode,
        'statusCode',
        404,
      )),
    );
  });
}
