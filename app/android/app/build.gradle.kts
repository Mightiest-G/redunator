import java.io.FileInputStream
import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// Release signing (apk-14): a stable keystore, injected at build time
// from GitHub Actions secrets (see .github/workflows/build-apk.yml).
// android/key.properties is gitignored and never committed; without it
// the build falls back to debug signing, so local dev still works.
// Every Actions build used to sign with a fresh debug key, which made
// Android refuse in-app updates ("package conflicts with an existing
// package", seen on apk-12 -> apk-13, 2026-11-19).
// Loaded at the top level (not inside android {}) — the canonical
// Flutter placement; inside android {} the java.* references do not
// resolve and the script fails to compile (run #14, 2026-11-20).
val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
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
        // Google Maps API key: injected at build time from the
        // MAPS_API_KEY environment variable (CI: the GOOGLE_MAPS_API_KEY
        // GitHub Actions secret, see .github/workflows/build-apk.yml).
        // The key used to be regex-read out of lib/config.dart, where
        // it lived as a committed const — it was flagged exposed twice
        // (GitGuardian) and rotated out (apk-19). Never put a real key
        // in this repo again. Local builds without the env var get an
        // empty key and the app shows the missing-key screen instead
        // of a blank map (see lib/widgets/missing_key_view.dart).
        // The Dart side reads the same value via --dart-define (see
        // lib/config.dart), so the manifest and the Dart check agree.
        manifestPlaceholders["MAPS_API_KEY"] =
            System.getenv("MAPS_API_KEY") ?: ""
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        // Created unconditionally so the block always exists; the
        // values are empty when key.properties is absent (local dev)
        // and the config is then never used (see buildTypes below).
        create("release") {
            keyAlias = keystoreProperties.getProperty("keyAlias", "")
            keyPassword = keystoreProperties.getProperty("keyPassword", "")
            storeFile = keystoreProperties.getProperty("storeFile")?.let {
                file(it)
            }
            storePassword = keystoreProperties.getProperty("storePassword", "")
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
