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
  static const String _kProAlertDistanceM = 'pro_alert_distance_m';
  static const String _kRiskThresholdPct = 'risk_threshold_pct';
  static const String _kBaseUrl = 'base_url';
  static const String _kUnits = 'units';
  static const String _kThemeMode = 'theme_mode';
  static const String _kSoundEnabled = 'sound_enabled';
  static const String _kBackgroundAlertsEnabled =
      'background_alerts_enabled';
  static const String _kMorningDigestEnabled = 'morning_digest_enabled';
  static const String _kSpokenAlertsEnabled = 'spoken_alerts_enabled';
  static const String _kWeeklyDigestEnabled = 'weekly_digest_enabled';

  /// Default alert distance: 300 m.
  static const double defaultAlertDistanceM = 300;

  /// Pro alert distance: how far out proximity alerts warn Pro users.
  /// Options 100 m / 250 m / 500 m / 1 km; default 250 m. Stored
  /// separately from the free slider (alertDistanceM) so a lapsed Pro
  /// subscription does not clobber the free setting.
  static const List<double> proAlertDistanceOptions = [100, 250, 500, 1000];
  static const double defaultProAlertDistanceM = 250;

  /// Default risk threshold: 75% (risk_score > 0.75 triggers alerts).
  static const double defaultRiskThresholdPct = 75;

  double alertDistanceM = defaultAlertDistanceM;
  double proAlertDistanceM = defaultProAlertDistanceM;
  double riskThresholdPct = defaultRiskThresholdPct;
  String baseUrl = defaultBaseUrl;
  String units = 'metric'; // metric | imperial
  String themeMode = 'system'; // system | light | dark

  /// Tap sounds on/off (UI feedback clicks only — the proximity alert
  /// sound is separate and always plays when an alert fires).
  bool soundEnabled = true;

  /// Background alerts (Pro): the 15-minute WorkManager check that
  /// watches for HIGH-risk cameras while the app is closed. Default
  /// ON — Pro users get the perk without digging, and it does
  /// nothing until the notification + "Allow all the time" location
  /// permissions are granted. The background isolate reads the same
  /// key directly (see background_alerts.dart).
  bool backgroundAlertsEnabled = true;

  /// Morning digest (Pro): the daily 07:00 risk summary for saved
  /// intersections. Default ON; the digest service reads the same
  /// key from the background isolate.
  bool morningDigestEnabled = true;

  /// Spoken alerts (Exclusive): a voice warning when the foreground
  /// proximity watcher fires. Default ON; only speaks for Exclusive
  /// users, and the toggle is Exclusive-gated in Settings.
  bool spokenAlertsEnabled = true;

  /// Weekly digest (Exclusive): the Sunday 07:00 week-in-review for
  /// saved intersections. Default ON; the digest service reads the
  /// same key from the background isolate.
  bool weeklyDigestEnabled = true;

  bool _loaded = false;
  bool get loaded => _loaded;

  static Future<AppSettings> load() async {
    final s = AppSettings._();
    final prefs = await SharedPreferences.getInstance();
    s.alertDistanceM =
        prefs.getDouble(_kAlertDistanceM) ?? defaultAlertDistanceM;
    final storedPro = prefs.getDouble(_kProAlertDistanceM);
    s.proAlertDistanceM =
        storedPro != null && proAlertDistanceOptions.contains(storedPro)
        ? storedPro
        : defaultProAlertDistanceM;
    s.riskThresholdPct =
        prefs.getDouble(_kRiskThresholdPct) ?? defaultRiskThresholdPct;
    s.baseUrl = prefs.getString(_kBaseUrl) ?? defaultBaseUrl;
    s.units = prefs.getString(_kUnits) ?? 'metric';
    s.themeMode = prefs.getString(_kThemeMode) ?? 'system';
    s.soundEnabled = prefs.getBool(_kSoundEnabled) ?? true;
    s.backgroundAlertsEnabled =
        prefs.getBool(_kBackgroundAlertsEnabled) ?? true;
    s.morningDigestEnabled =
        prefs.getBool(_kMorningDigestEnabled) ?? true;
    s.spokenAlertsEnabled =
        prefs.getBool(_kSpokenAlertsEnabled) ?? true;
    s.weeklyDigestEnabled =
        prefs.getBool(_kWeeklyDigestEnabled) ?? true;
    s._loaded = true;
    return s;
  }

  /// Test/dev helper: build settings without touching storage.
  static AppSettings withValues({
    double alertDistanceM = defaultAlertDistanceM,
    double proAlertDistanceM = defaultProAlertDistanceM,
    double riskThresholdPct = defaultRiskThresholdPct,
    String baseUrl = defaultBaseUrl,
    String units = 'metric',
    String themeMode = 'system',
    bool soundEnabled = true,
    bool backgroundAlertsEnabled = true,
    bool morningDigestEnabled = true,
    bool spokenAlertsEnabled = true,
    bool weeklyDigestEnabled = true,
  }) {
    final s = AppSettings._()
      ..alertDistanceM = alertDistanceM
      ..proAlertDistanceM = proAlertDistanceM
      ..riskThresholdPct = riskThresholdPct
      ..baseUrl = baseUrl
      ..units = units
      ..themeMode = themeMode
      ..soundEnabled = soundEnabled
      ..backgroundAlertsEnabled = backgroundAlertsEnabled
      ..morningDigestEnabled = morningDigestEnabled
      ..spokenAlertsEnabled = spokenAlertsEnabled
      ..weeklyDigestEnabled = weeklyDigestEnabled
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
    await prefs.setDouble(_kProAlertDistanceM, proAlertDistanceM);
    await prefs.setDouble(_kRiskThresholdPct, riskThresholdPct);
    await prefs.setString(_kBaseUrl, baseUrl);
    await prefs.setString(_kUnits, units);
    await prefs.setString(_kThemeMode, themeMode);
    await prefs.setBool(_kSoundEnabled, soundEnabled);
    await prefs.setBool(_kBackgroundAlertsEnabled, backgroundAlertsEnabled);
    await prefs.setBool(_kMorningDigestEnabled, morningDigestEnabled);
    await prefs.setBool(_kSpokenAlertsEnabled, spokenAlertsEnabled);
    await prefs.setBool(_kWeeklyDigestEnabled, weeklyDigestEnabled);
  }

  Future<void> setAlertDistanceM(double v) async {
    alertDistanceM = v.clamp(50, 5000);
    await _save();
    notifyListeners();
  }

  /// Pro alert distance. Only the four offered values stick; anything
  /// else is ignored (the stored value never becomes invalid).
  Future<void> setProAlertDistanceM(double v) async {
    if (!proAlertDistanceOptions.contains(v)) return;
    proAlertDistanceM = v;
    await _save();
    notifyListeners();
  }

  /// The alert distance the proximity watcher should use right now:
  /// the Pro distance for Pro users, the free slider otherwise.
  double alertDistanceFor(bool isPro) =>
      isPro ? proAlertDistanceM : alertDistanceM;

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

  /// Theme preference: 'system' (follow the phone), 'light', 'dark'.
  /// Anything else falls back to 'system'.
  Future<void> setThemeMode(String v) async {
    themeMode = (v == 'light' || v == 'dark') ? v : 'system';
    await _save();
    notifyListeners();
  }

  /// Tap sounds on/off. Applies immediately to the shared SoundService
  /// via the widget tree (the service reads settings.soundEnabled).
  Future<void> setSoundEnabled(bool v) async {
    soundEnabled = v;
    await _save();
    notifyListeners();
  }

  /// Background alerts toggle (Pro). The WorkManager task is
  /// (un)registered by the caller after this saves.
  Future<void> setBackgroundAlertsEnabled(bool v) async {
    backgroundAlertsEnabled = v;
    await _save();
    notifyListeners();
  }

  /// Morning digest toggle (Pro). The digest is (un)scheduled by
  /// the caller after this saves.
  Future<void> setMorningDigestEnabled(bool v) async {
    morningDigestEnabled = v;
    await _save();
    notifyListeners();
  }

  /// Spoken alerts toggle (Exclusive). The foreground watcher reads
  /// it via the widget tree after this saves.
  Future<void> setSpokenAlertsEnabled(bool v) async {
    spokenAlertsEnabled = v;
    await _save();
    notifyListeners();
  }

  /// Weekly digest toggle (Exclusive). The digest is (un)scheduled
  /// by the caller after this saves.
  Future<void> setWeeklyDigestEnabled(bool v) async {
    weeklyDigestEnabled = v;
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
