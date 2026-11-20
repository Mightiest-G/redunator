plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.redunator.redunator"
    // compileSdk 37 (not flutter.compileSdkVersion's 36):
    // flutter_local_notifications requires compiling against Android
    // API 37+ and core library desugaring (apk-8 build failed on
    // checkReleaseAarMetadata without both). targetSdk stays at
    // flutter.targetSdkVersion (36) — the Play Store requirement.
    compileSdk = 37
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // Required by flutter_local_notifications (see compileSdk note).
        isCoreLibraryDesugaringEnabled = true
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

    // Release signing (apk-14): a stable keystore, injected at build
    // time from GitHub Actions secrets (see .github/workflows/build-apk.yml).
    // android/key.properties is gitignored and never committed; without
    // it the build falls back to debug signing, so local dev still works.
    // Every Actions build used to sign with a fresh debug key, which made
    // Android refuse in-app updates ("package conflicts with an existing
    // package", seen on apk-12 -> apk-13, 2026-11-19).
    val keystoreProperties = java.util.Properties()
    val keystorePropertiesFile = rootProject.file("key.properties")
    if (keystorePropertiesFile.exists()) {
        keystoreProperties.load(java.io.FileInputStream(keystorePropertiesFile))
        signingConfigs {
            create("release") {
                keyAlias = keystoreProperties["keyAlias"] as String
                keyPassword = keystoreProperties["keyPassword"] as String
                storeFile = file(keystoreProperties["storeFile"] as String)
                storePassword = keystoreProperties["storePassword"] as String
            }
        }
    }

    buildTypes {
        release {
            // Stable release key when key.properties exists (CI);
            // debug signing otherwise (local dev).
            signingConfig = if (keystorePropertiesFile.exists()) {
                signingConfigs.getByName("release")
            } else {
                signingConfigs.getByName("debug")
            }
        }
    }
}

dependencies {
    // Core library desugaring runtime, required by
    // flutter_local_notifications (see compileSdk note above).
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
