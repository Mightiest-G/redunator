// Home-screen risk widget (Exclusive): an Android app widget that
// shows today's risk for the user's saved intersections without
// opening the app. The Dart side writes the text into the widget's
// data store; the Android provider (RiskWidgetProvider, Kotlin)
// renders it. Updates are pushed, never polled: the app refreshes
// the widget on launch, after the morning digest refreshes, and
// from the 15-minute background run — so the widget stays fresh
// even when the app has not been opened today.
//
// The widget shows at most 3 saved cameras (highest risk first)
// plus an "updated" stamp. No saved intersections -> a prompt to
// save some. Everything is best-effort and swallowed: a widget
// failure never breaks the app or the background task.
import 'package:home_widget/home_widget.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api/client.dart';
import 'saved_service.dart';

/// Keys the Kotlin provider reads from the widget data store.
const String kWidgetTitleKey = 'redunator_widget_title';
const String kWidgetBodyKey = 'redunator_widget_body';

/// Android widget provider class (see android/app/src/main/...).
const String kWidgetAndroidName = 'RiskWidgetProvider';
const String kWidgetQualifiedAndroidName =
    'com.redunator.redunator.RiskWidgetProvider';

/// Seam over the home_widget plugin so tests can fake it.
abstract class WidgetStore {
  Future<void> saveData(Map<String, String> data);
  Future<void> updateWidget();
}

class HomeWidgetStore implements WidgetStore {
  @override
  Future<void> saveData(Map<String, String> data) async {
    for (final e in data.entries) {
      await HomeWidget.saveWidgetData<String>(e.key, e.value);
    }
  }

  @override
  Future<void> updateWidget() => HomeWidget.updateWidget(
    androidName: kWidgetAndroidName,
    qualifiedAndroidName: kWidgetQualifiedAndroidName,
  );
}

/// Pure widget body builder (unit-tested). Rows are highest risk
/// first, at most [maxRows]; the footer always carries the update
/// time so a stale widget is obvious.
String buildWidgetText({
  required List<({String name, double daily, String band})> rows,
  required DateTime now,
}) {
  if (rows.isEmpty) {
    return 'No saved intersections yet — save cameras on your route '
        'and they will show here.';
  }
  final sorted = List.of(rows)
    ..sort((a, b) => b.daily.compareTo(a.daily));
  final lines = <String>[];
  for (final r in sorted.take(3)) {
    lines.add(
      '${r.name}: ${r.daily.toStringAsFixed(1)}/day ${r.band}',
    );
  }
  if (sorted.length > 3) {
    lines.add('…and ${sorted.length - 3} more');
  }
  final hh = now.hour.toString().padLeft(2, '0');
  final mm = now.minute.toString().padLeft(2, '0');
  lines.add('Updated $hh:$mm · estimates');
  return lines.join('\n');
}

class WidgetService {
  WidgetService({WidgetStore? store}) : _store = store ?? HomeWidgetStore();

  final WidgetStore _store;

  /// Refreshes the widget from the persisted saved list + one
  /// batch prediction. Reads everything from SharedPreferences so
  /// it works from the foreground app and the background isolate.
  /// Never throws.
  Future<void> refresh({required RedunatorApiClient client}) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = SavedService.readSavedStatic(prefs);
      var body = 'No saved intersections yet — save cameras on your '
          'route and they will show here.';
      if (saved.isNotEmpty) {
        final batch = await client.predictRiskBatch(
          saved.map((s) => {'camera_id': s.cameraId}).toList(),
        );
        final byId = {for (final s in saved) s.cameraId: s};
        final rows = <({String name, double daily, String band})>[];
        for (final p in batch.results) {
          final s = byId[p.cameraId];
          if (s == null) continue;
          rows.add((
            name: s.intersection,
            daily: p.dailyPrediction,
            band: p.riskLevel,
          ));
        }
        body = buildWidgetText(rows: rows, now: DateTime.now());
      }
      await _store.saveData({
        kWidgetTitleKey: 'Redunator — today',
        kWidgetBodyKey: body,
      });
      await _store.updateWidget();
    } catch (_) {
      // Best-effort only.
    }
  }
}
