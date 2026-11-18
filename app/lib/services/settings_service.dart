// App settings, persisted with shared_preferences.
// These are LOCAL app settings (alert distance, threshold, base URL,
// units, theme). They are separate from the backend's /user_settings,
// which the app also reads/writes (see RedunatorApiClient) — the backend
// copy is the server-side record; this copy drives the app UI.
import 'package:flutter/material.dart'; // ignore: deprecated_member_use
import 'package:shared_preferences/shared_preferences.dart';

import '../config.dart';

class AppSettings extends ChangeNotifier {
  AppSettings._();

  static const String _kAlertDistanceM = 'alert_distance_m';
  static const String _kRiskThresholdPct = 'risk_threshold_pct';
  static const String _kBaseUrl = 'base_url';
  static const String _kUnits = 'units';
  static const String _kUserId = 'user_id';
  static const String _kThemeMode = 'theme_mode';

  /// Default alert distance: 300 m.
  static const double defaultAlertDistanceM = 300;

  /// Default risk threshold: 75% (risk_score > 0.75 triggers alerts).
  static const double defaultRiskThresholdPct = 75;

  double alertDistanceM = defaultAlertDistanceM;
  double riskThresholdPct = defaultRiskThresholdPct;
  String baseUrl = defaultBaseUrl;
  String units = 'metric'; // metric | imperial
  String userId = 'anonymous';
  String themeMode = 'system'; // system | light | dark

  bool _loaded = false;
  bool get loaded => _loaded;

  static Future<AppSettings> load() async {
    final s = AppSettings._();
    final prefs = await SharedPreferences.getInstance();
    s.alertDistanceM =
        prefs.getDouble(_kAlertDistanceM) ?? defaultAlertDistanceM;
    s.riskThresholdPct =
        prefs.getDouble(_kRiskThresholdPct) ?? defaultRiskThresholdPct;
    s.baseUrl = prefs.getString(_kBaseUrl) ?? defaultBaseUrl;
    s.units = prefs.getString(_kUnits) ?? 'metric';
    s.userId = prefs.getString(_kUserId) ?? 'anonymous';
    s.themeMode = prefs.getString(_kThemeMode) ?? 'system';
    s._loaded = true;
    return s;
  }

  /// Test/dev helper: build settings without touching storage.
  static AppSettings withValues({
    double alertDistanceM = defaultAlertDistanceM,
    double riskThresholdPct = defaultRiskThresholdPct,
    String baseUrl = defaultBaseUrl,
    String units = 'metric',
    String userId = 'anonymous',
    String themeMode = 'system',
  }) {
    final s = AppSettings._()
      ..alertDistanceM = alertDistanceM
      ..riskThresholdPct = riskThresholdPct
      ..baseUrl = baseUrl
      ..units = units
      ..userId = userId
      ..themeMode = themeMode
      .._loaded = true;
    return s;
  }

  /// The Flutter ThemeMode for MaterialApp. Unknown stored values fall
  /// back to following the phone.
  ThemeMode get themeModeEnum {
    switch (themeMode) {
      case 'light':
        return ThemeMode.light;
      case 'dark':
        return ThemeMode.dark;
      default:
        return ThemeMode.system;
    }
  }

  /// Whether the UI (including the map) should render dark right now:
  /// explicit light/dark wins, otherwise the phone's system setting.
  bool isDark(BuildContext context) {
    if (themeMode == 'dark') return true;
    if (themeMode == 'light') return false;
    return MediaQuery.platformBrightnessOf(context) == Brightness.dark;
  }

  double get riskThresholdScore => riskThresholdPct / 100;

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_kAlertDistanceM, alertDistanceM);
    await prefs.setDouble(_kRiskThresholdPct, riskThresholdPct);
    await prefs.setString(_kBaseUrl, baseUrl);
    await prefs.setString(_kUnits, units);
    await prefs.setString(_kUserId, userId);
    await prefs.setString(_kThemeMode, themeMode);
  }

  Future<void> setAlertDistanceM(double v) async {
    alertDistanceM = v.clamp(50, 5000);
    await _save();
    notifyListeners();
  }

  Future<void> setRiskThresholdPct(double v) async {
    riskThresholdPct = v.clamp(10, 100);
    await _save();
    notifyListeners();
  }

  Future<void> setBaseUrl(String v) async {
    baseUrl = v.trim().isEmpty ? defaultBaseUrl : v.trim();
    await _save();
    notifyListeners();
  }

  Future<void> setUnits(String v) async {
    units = v == 'imperial' ? 'imperial' : 'metric';
    await _save();
    notifyListeners();
  }

  Future<void> setUserId(String v) async {
    userId = v.trim().isEmpty ? 'anonymous' : v.trim();
    await _save();
    notifyListeners();
  }

  /// Theme preference: 'system' (follow the phone), 'light', 'dark'.
  /// Anything else falls back to 'system'.
  Future<void> setThemeMode(String v) async {
    themeMode = (v == 'light' || v == 'dark') ? v : 'system';
    await _save();
    notifyListeners();
  }

  String formatDistance(double metres) {
    if (units == 'imperial') {
      final miles = metres / 1609.344;
      return miles >= 0.1
          ? '${miles.toStringAsFixed(2)} mi'
          : '${(metres * 3.28084).round()} ft';
    }
    return metres >= 1000
        ? '${(metres / 1000).toStringAsFixed(2)} km'
        : '${metres.round()} m';
  }
}
