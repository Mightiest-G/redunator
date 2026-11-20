// Home-screen widget (Exclusive): the body text builder and the
// refresh flow against a fake widget store + mock HTTP.
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:redunator/api/client.dart';
import 'package:redunator/services/widget_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeStore implements WidgetStore {
  Map<String, String> data = {};
  int updateCalls = 0;

  @override
  Future<void> saveData(Map<String, String> data) async {
    this.data = Map.of(data);
  }

  @override
  Future<void> updateWidget() async {
    updateCalls++;
  }
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
  'risk_score': 1.0,
  'risk_level': band,
  'confidence': 0.9,
  'reasons': [],
  'hourly': [],
  'hourly_estimate_note': '',
  'hourly_method': '',
  'hourly_sum': 0.0,
  'reconciliation_error': 0.0,
  'model_version': 1,
  'nearest_distance_m': null,
};

RedunatorApiClient _batchClient() => RedunatorApiClient(
  baseUrl: 'https://example.invalid',
  httpClient: MockClient((req) async {
    if (req.url.path == '/predict_risk/batch') {
      final body = jsonDecode(req.body) as Map<String, dynamic>;
      final ids = (body['items'] as List)
          .map((c) => (c as Map)['camera_id'] as String)
          .toList();
      return http.Response(
        jsonEncode({
          'results': ids.map((id) {
            final daily = id == '1413' ? 53.0 : 2.0;
            final band = id == '1413' ? 'HIGH' : 'LOW';
            return _predictionJson(
              cameraId: id,
              name: id == '1413' ? 'BELMONT' : 'OTHER ST',
              daily: daily,
              band: band,
            );
          }).toList(),
          'count': ids.length,
          'errors': 0,
        }),
        200,
      );
    }
    return http.Response('not found', 404);
  }),
);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('buildWidgetText', () {
    test('sorts worst-first and stamps the update time', () {
      final text = buildWidgetText(
        rows: [
          (name: 'LOW ST', daily: 2.0, band: 'LOW'),
          (name: 'HIGH ST', daily: 53.0, band: 'HIGH'),
        ],
        now: DateTime(2026, 11, 19, 7, 5),
      );
      expect(text.indexOf('HIGH ST'), lessThan(text.indexOf('LOW ST')));
      expect(text, contains('53.0/day HIGH'));
      expect(text, contains('Updated 07:05 · estimates'));
    });

    test('at most three rows, then a "+N more" line', () {
      final text = buildWidgetText(
        rows: [
          for (var i = 0; i < 5; i++)
            (name: 'CAM $i', daily: 10.0 - i, band: 'HIGH'),
        ],
        now: DateTime(2026, 11, 19, 7, 0),
      );
      expect(text, contains('CAM 0'));
      expect(text, contains('CAM 2'));
      expect(text, contains('…and 2 more'));
      expect(text, isNot(contains('CAM 3')));
    });

    test('no saved cameras prompts to save some', () {
      final text = buildWidgetText(
        rows: [],
        now: DateTime(2026, 11, 19, 7, 0),
      );
      expect(text, contains('No saved intersections yet'));
    });
  });

  group('WidgetService.refresh', () {
    test('writes the top risks and updates the widget', () async {
      SharedPreferences.setMockInitialValues({
        'saved_intersections': jsonEncode([
          {
            'camera_id': '1413',
            'intersection': 'BELMONT',
            'latitude': 41.940241,
            'longitude': -87.639639,
            'server_alert_id': null,
          },
          {
            'camera_id': '1414',
            'intersection': 'OTHER ST',
            'latitude': 41.9,
            'longitude': -87.6,
            'server_alert_id': null,
          },
        ]),
      });
      final store = _FakeStore();
      final service = WidgetService(store: store);
      await service.refresh(client: _batchClient());
      expect(store.data[kWidgetTitleKey], 'Redunator — today');
      expect(store.data[kWidgetBodyKey], contains('BELMONT: 53.0/day HIGH'));
      expect(store.updateCalls, 1);
    });

    test('no saved cameras writes the prompt text', () async {
      SharedPreferences.setMockInitialValues({});
      final store = _FakeStore();
      final service = WidgetService(store: store);
      await service.refresh(client: _batchClient());
      expect(
        store.data[kWidgetBodyKey],
        contains('No saved intersections yet'),
      );
      expect(store.updateCalls, 1);
    });
  });
}
