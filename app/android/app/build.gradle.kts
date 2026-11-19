plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.redunator.redunator"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.redunator.redunator"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        // minSdk 23 (not flutter.minSdkVersion's 21): workmanager's
        // Android library requires 23. Safe for a modern app.
        minSdk = 23
        targetSdk = flutter.targetSdkVersion
        // Google Maps API key: single source of truth is lib/config.dart
        // (const String googleMapsApiKey). Read it here so the Android
        // manifest gets the same value the Dart code checks.
        // Anchored to the real `const String` declaration: the comment
        // block above it used to contain an example assignment, and an
        // unanchored regex matched that example first, injecting the
        // literal placeholder 'AIza...' into the manifest — which made
        // the map stay blank on device (2026-11-17).
        val configFile = rootProject.file("../lib/config.dart")
        var mapsApiKey = ""
        if (configFile.exists()) {
            val match = Regex("""(?m)^const String googleMapsApiKey\s*=\s*['"]([^'"]*)['"]""")
                .find(configFile.readText())
            if (match != null) mapsApiKey = match.groupValues[1]
        }
        manifestPlaceholders["MAPS_API_KEY"] = mapsApiKey
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
