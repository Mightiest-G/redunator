// Area watch (Exclusive): the pure geometry + alert targeting, the
// one-alert-per-area-per-day dedupe, and service persistence.
import 'package:flutter_test/flutter_test.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/area_watch_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

Intersection _cam({
  String id = '1413',
  double lat = 41.940241,
  double lng = -87.639639,
}) => Intersection(
  cameraId: id,
  intersection: 'CAM $id',
  latitude: lat,
  longitude: lng,
  typicalWeekdayPred: 53,
  typicalWeekendPred: 40,
  typicalWeekdayBand: 'HIGH',
  typicalWeekendBand: 'HIGH',
  typicalWeekdayScore: 1.0,
  typicalWeekendScore: 0.9,
  actualTestMean: null,
  camMeanPreTest: null,
);

Prediction _pred({
  required String id,
  String? name,
  double daily = 53.0,
  String band = 'HIGH',
}) => Prediction(
  cameraId: id,
  intersection: name ?? 'CAM $id',
  latitude: 41.940241,
  longitude: -87.639639,
  date: '2026-10-15',
  timestamp: '2026-10-15T12:00:00',
  dailyPrediction: daily,
  dailySource: 'model',
  riskScore: 1.0,
  riskLevel: band,
  confidence: 0.9,
  reasons: const [],
  hourly: const [],
  hourlyEstimateNote: '',
  hourlyMethod: '',
  hourlySum: 0,
  reconciliationError: 0,
  modelVersion: 1,
  nearestDistanceM: null,
);

WatchArea _area({double radiusM = 1000}) => WatchArea(
  id: 'area-1',
  name: 'Home',
  latitude: 41.940241,
  longitude: -87.639639,
  radiusM: radiusM,
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('camerasInArea', () {
    test('keeps cameras inside the radius, drops the rest', () {
      final cams = [
        _cam(id: '1413'), // at the centre
        _cam(id: '1414', lat: 41.949, lng: -87.639639), // ~970 m north
        _cam(id: '1415', lat: 41.970, lng: -87.639639), // ~3.3 km north
        _cam(id: '1416', lat: 41.0, lng: -87.0), // far away
      ];
      final inside = camerasInArea(cams, _area());
      expect(inside.map((c) => c.cameraId), ['1413', '1414']);
    });

    test('a camera without coordinates is never inside', () {
      final cam = Intersection(
        cameraId: '1417',
        intersection: 'NO COORDS',
        latitude: null,
        longitude: null,
        typicalWeekdayPred: 1,
        typicalWeekendPred: 1,
        typicalWeekdayBand: 'LOW',
        typicalWeekendBand: 'LOW',
        typicalWeekdayScore: 0,
        typicalWeekendScore: 0,
        actualTestMean: null,
        camMeanPreTest: null,
      );
      expect(camerasInArea([cam], _area()), isEmpty);
    });
  });

  group('areaAlertTargets', () {
    test('alerts only when a HIGH camera is inside the area', () {
      final cams = [_cam(id: '1413'), _cam(id: '1414', lat: 41.949)];
      final predictions = {
        '1413': _pred(id: '1413', daily: 53.0, band: 'HIGH'),
        '1414': _pred(id: '1414', daily: 3.0, band: 'MEDIUM'),
      };
      final alerts = areaAlertTargets(
        areas: [_area()],
        cameras: cams,
        predictions: predictions,
      );
      expect(alerts.length, 1);
      expect(alerts.first.highCameras.length, 1);
      expect(alerts.first.highCameras.first.cameraId, '1413');
    });

    test('no HIGH cameras means no alert', () {
      final alerts = areaAlertTargets(
        areas: [_area()],
        cameras: [_cam()],
        predictions: {'1413': _pred(id: '1413', band: 'MEDIUM')},
      );
      expect(alerts, isEmpty);
    });

    test('HIGH cameras sort worst-first', () {
      final cams = [
        _cam(id: '1413'),
        _cam(id: '1414', lat: 41.949),
      ];
      final alerts = areaAlertTargets(
        areas: [_area()],
        cameras: cams,
        predictions: {
          '1413': _pred(id: '1413', daily: 20.0),
          '1414': _pred(id: '1414', daily: 53.0),
        },
      );
      expect(alerts.first.highCameras.first.cameraId, '1414');
    });

    test('buildAreaAlertText names the worst camera and the extras', () {
      final alerts = areaAlertTargets(
        areas: [_area()],
        cameras: [_cam(id: '1413'), _cam(id: '1414', lat: 41.949)],
        predictions: {
          '1413': _pred(id: '1413', name: 'LAKE SHORE DR', daily: 20.0),
          '1414': _pred(id: '1414', name: 'BELMONT', daily: 53.0),
        },
      );
      final text = buildAreaAlertText(alerts.first);
      expect(text, contains('Home'));
      expect(text, contains('BELMONT'));
      expect(text, contains('53.0/day'));
      expect(text, contains('+1 more HIGH'));
    });
  });

  group('AreaAlertDedupe', () {
    test('one alert per area per day', () async {
      SharedPreferences.setMockInitialValues({});
      final prefs = await SharedPreferences.getInstance();
      final dedupe = AreaAlertDedupe(prefs);
      final day = DateTime(2026, 11, 19, 9, 0);
      expect(dedupe.shouldAlert('area-1', day), isTrue);
      await dedupe.recordAlert('area-1', day);
      expect(dedupe.shouldAlert('area-1', day), isFalse);
      // Next day it may alert again; other areas are unaffected.
      expect(dedupe.shouldAlert('area-1', DateTime(2026, 11, 20)), isTrue);
      expect(dedupe.shouldAlert('area-2', day), isTrue);
    });
  });

  group('AreaWatchService persistence', () {
    test('add / load / remove round-trips through SharedPreferences',
        () async {
      SharedPreferences.setMockInitialValues({});
      final service = AreaWatchService();
      await service.load();
      expect(service.areas, isEmpty);
      final area = await service.add(
        name: 'Home',
        latitude: 41.940241,
        longitude: -87.639639,
        radiusM: 2000,
      );
      expect(area.name, 'Home');
      expect(area.radiusM, 2000);

      final reloaded = AreaWatchService();
      await reloaded.load();
      expect(reloaded.areas.length, 1);
      expect(reloaded.areas.first.name, 'Home');

      await reloaded.remove(area.id);
      final empty = AreaWatchService();
      await empty.load();
      expect(empty.areas, isEmpty);
    });
  });
}
