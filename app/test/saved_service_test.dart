// Saved intersections: local persistence is the source of truth and
// the /user_alerts mirror is best-effort — a server failure must never
// lose a save, and unsaving must delete the mirrored alert.
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/api/models.dart';
import 'package:redunator/services/saved_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

Intersection _cam({String id = '1413'}) => Intersection(
  cameraId: id,
  intersection: 'LAKE SHORE DR AND BELMONT',
  latitude: 41.940241,
  longitude: -87.639639,
  typicalWeekdayPred: 53,
  typicalWeekendPred: 40,
  typicalWeekdayBand: 'HIGH',
  typicalWeekendBand: 'HIGH',
  typicalWeekdayScore: 1.0,
  typicalWeekendScore: 0.9,
  actualTestMean: null,
  camMeanPreTest: null,
);

Map<String, dynamic> _alertJson({int id = 7}) => {
  'id': id,
  'latitude': 41.940241,
  'longitude': -87.639639,
  'radius_m': 2000,
  'threshold': 'MEDIUM',
  'threshold_value': null,
  'name': 'LAKE SHORE DR AND BELMONT',
  'active': true,
};

RedunatorApiClient _alertClient({
  required bool failCreate,
  List<String>? seen,
}) => RedunatorApiClient(
  baseUrl: 'https://example.invalid',
  httpClient: MockClient((req) async {
    seen?.add('${req.method} ${req.url.path}');
    if (req.method == 'POST' && req.url.path == '/user_alerts') {
      if (failCreate) return http.Response('{"error":"boom"}', 500);
      return http.Response(jsonEncode(_alertJson()), 201);
    }
    if (req.method == 'DELETE' && req.url.path == '/user_alerts/7') {
      return http.Response(jsonEncode(_alertJson()), 200);
    }
    return http.Response('not found', 404);
  }),
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('SavedService', () {
    test('save persists locally and mirrors to the server', () async {
      final seen = <String>[];
      final svc = SavedService(
        client: _alertClient(failCreate: false, seen: seen),
      );
      await svc.load();
      await svc.save(_cam());
      expect(svc.isSaved('1413'), isTrue);
      expect(svc.get('1413')?.serverAlertId, 7);
      expect(seen, contains('POST /user_alerts'));
      // A second service instance restores the save from storage.
      final svc2 = SavedService(client: _alertClient(failCreate: false));
      await svc2.load();
      expect(svc2.isSaved('1413'), isTrue);
      expect(svc2.get('1413')?.intersection, 'LAKE SHORE DR AND BELMONT');
    });

    test('save with a failing server still persists locally', () async {
      final svc = SavedService(client: _alertClient(failCreate: true));
      await svc.load();
      await svc.save(_cam());
      expect(svc.isSaved('1413'), isTrue);
      // Never mirrored: no server id, retried on the next load.
      expect(svc.get('1413')?.serverAlertId, isNull);
    });

    test('save works with no client at all (local only)', () async {
      final svc = SavedService();
      await svc.load();
      await svc.save(_cam());
      expect(svc.isSaved('1413'), isTrue);
    });

    test('unsave removes locally and deletes the server mirror', () async {
      final seen = <String>[];
      final svc = SavedService(
        client: _alertClient(failCreate: false, seen: seen),
      );
      await svc.load();
      await svc.save(_cam());
      expect(svc.get('1413')?.serverAlertId, 7);
      await svc.unsave('1413');
      expect(svc.isSaved('1413'), isFalse);
      expect(seen, contains('DELETE /user_alerts/7'));
    });

    test('canSaveMore enforces the tier caps (Free 3 / Pro 10)', () async {
      // canSaveMore is an instance check against the loaded list:
      // Free's cap is 3, Pro's is 10, Exclusive (null) is unlimited.
      SharedPreferences.setMockInitialValues({
        'saved_intersections': jsonEncode([
          for (var i = 0; i < 3; i++)
            {
              'camera_id': '14${10 + i}',
              'intersection': 'CAM $i',
              'latitude': 41.9,
              'longitude': -87.6,
              'server_alert_id': null,
            },
        ]),
      });
      final svc = SavedService();
      await svc.load();
      expect(svc.saved.length, 3);
      expect(svc.canSaveMore(3), isFalse);
      expect(svc.canSaveMore(10), isTrue);
      expect(svc.canSaveMore(null), isTrue);
    });

    test('saving twice is a no-op', () async {
      final seen = <String>[];
      final svc = SavedService(
        client: _alertClient(failCreate: false, seen: seen),
      );
      await svc.load();
      await svc.save(_cam());
      await svc.save(_cam());
      expect(svc.saved.length, 1);
      expect(seen.where((s) => s == 'POST /user_alerts').length, 1);
    });
  });
}
