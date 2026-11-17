// Proximity alert logic tests: distance + threshold + cooldown.
// Uses checkPosition (no platform location needed).
import 'package:flutter_test/flutter_test.dart';
import 'package:geolocator/geolocator.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/proximity_service.dart';

Intersection _cam(
  String id, {
  required double lat,
  required double lon,
  double score = 1.0,
}) =>
    Intersection(
      cameraId: id,
      intersection: 'CAM $id',
      latitude: lat,
      longitude: lon,
      typicalWeekdayPred: 35,
      typicalWeekendPred: 35,
      typicalWeekdayBand: 'HIGH',
      typicalWeekendBand: 'HIGH',
      typicalWeekdayScore: score,
      typicalWeekendScore: score,
      actualTestMean: null,
      camMeanPreTest: null,
    );

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

void main() {
  test('alerts within 300 m of a >75% camera, not beyond', () {
    final svc = ProximityService();
    final cameras = [_cam('1413', lat: 41.940241, lon: -87.639639)];
    // ~100 m north of the camera.
    final near = svc.checkPosition(
      _pos(41.941141, -87.639639),
      cameras: cameras,
      predictions: {},
      alertDistanceM: 300,
      riskThresholdScore: 0.75,
    );
    expect(near?.cameraId, '1413');
    // ~1 km north — outside the alert distance.
    final far = svc.checkPosition(
      _pos(41.949241, -87.639639),
      cameras: cameras,
      predictions: {},
      alertDistanceM: 300,
      riskThresholdScore: 0.75,
    );
    expect(far, isNull);
  });

  test('no alert when the camera score is below the threshold', () {
    final svc = ProximityService();
    final cameras = [
      _cam('1413', lat: 41.940241, lon: -87.639639, score: 0.5),
    ];
    final hit = svc.checkPosition(
      _pos(41.941141, -87.639639),
      cameras: cameras,
      predictions: {},
      alertDistanceM: 300,
      riskThresholdScore: 0.75,
    );
    expect(hit, isNull);
  });

  test('batch prediction score overrides the typical score', () {
    final svc = ProximityService();
    final cameras = [
      _cam('1413', lat: 41.940241, lon: -87.639639, score: 0.1),
    ];
    // Typical score is low, but today's prediction is high.
    final pred = Prediction.fromJson({
      'camera_id': '1413',
      'intersection': 'LAKE SHORE DR AND BELMONT',
      'latitude': 41.940241,
      'longitude': -87.639639,
      'date': '2026-10-15',
      'timestamp': null,
      'daily_prediction': 53.0,
      'daily_source': 'model',
      'risk_score': 1.0,
      'risk_level': 'HIGH',
      'confidence': 0.9,
      'reasons': [],
      'hourly': [],
      'hourly_estimate_note': '',
      'hourly_method': 'model',
      'hourly_sum': 53.0,
      'reconciliation_error': 0.0,
      'model_version': 1,
      'nearest_distance_m': null,
    });
    final hit = svc.checkPosition(
      _pos(41.941141, -87.639639),
      cameras: cameras,
      predictions: {'1413': pred},
      alertDistanceM: 300,
      riskThresholdScore: 0.75,
    );
    expect(hit?.cameraId, '1413');
  });
}
