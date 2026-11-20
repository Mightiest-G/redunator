// Opens a downloaded APK with the system installer via a tiny
// MethodChannel in MainActivity (see MainActivity.kt). This replaces
// open_filex, whose Android module (old-style buildscript pinning
// AGP 8.1.0) would not resolve under this project's AGP 9.3.1 /
// Kotlin 2.4.20 setup and broke the apk-11 Actions build. The channel
// is deliberately small: one method, one string result.
import 'package:flutter/services.dart';

class ApkInstaller {
  ApkInstaller({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel('redunator/install');

  final MethodChannel _channel;

  /// Returns 'ok' when the installer opened, otherwise a short error
  /// message for the UI. Never throws.
  Future<String> installApk(String path) async {
    try {
      final value = await _channel.invokeMethod<String>(
        'installApk',
        {'path': path},
      );
      return value ?? 'Could not open the installer.';
    } on PlatformException catch (e) {
      return e.message ?? 'Could not open the installer.';
    } catch (_) {
      return 'Could not open the installer.';
    }
  }
}
