package com.redunator.redunator

import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            "redunator/install",
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "installApk" -> {
                    val path = call.argument<String>("path")
                    if (path == null) {
                        result.error("NO_PATH", "No APK path given.", null)
                    } else {
                        result.success(installApk(path))
                    }
                }
                else -> result.notImplemented()
            }
        }
    }

    // Shares the downloaded APK with the system installer through the
    // FileProvider declared in AndroidManifest.xml (authority
    // "<package>.fileprovider", paths in res/xml/filepaths.xml).
    // Returns "ok" when the installer opened, otherwise an error
    // string for the Dart side to show. Android 8+ shows a one-time
    // "allow installs from Redunator" prompt when the installer opens.
    private fun installApk(path: String): String {
        return try {
            val file = File(path)
            if (!file.exists()) return "The downloaded file is missing."
            val uri: Uri = FileProvider.getUriForFile(
                this,
                "${applicationContext.packageName}.fileprovider",
                file,
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            startActivity(intent)
            "ok"
        } catch (e: Exception) {
            "Could not open the installer: ${e.message}"
        }
    }
}
