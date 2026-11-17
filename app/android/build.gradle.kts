allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

val newBuildDir: Directory =
    rootProject.layout.buildDirectory
        .dir("../../build")
        .get()
rootProject.layout.buildDirectory.value(newBuildDir)

subprojects {
    val newSubprojectBuildDir: Directory = newBuildDir.dir(project.name)
    project.layout.buildDirectory.value(newSubprojectBuildDir)
}
// Worker 3 (2026-11-16): release lint is disabled for every Android
// module. AGP 9.3.1's lint crashes the RELEASE build on this VM's JDK 17:
// `:google_maps_flutter_android:lintVitalAnalyzeRelease` dies with
// `NoSuchMethodError: java.util.List.removeLast()` (a Java 21 API) inside
// lint's JavaDocParser while parsing that plugin's Convert.java. That is
// a crash in the lint TOOL, not a finding in our code — the debug build
// passes and `flutter analyze` is clean. Only JDK 17 exists on this VM,
// so until the toolchain moves to JDK 21 (or a fixed AGP), release lint
// stays off. `flutter analyze` + the unit tests remain the quality gate.
// NOTE: this block MUST stay above the `evaluationDependsOn(":app")`
// block below — AGP reads checkReleaseBuilds during :app's evaluation,
// and configuring it afterwards fails with "It is too late to set
// checkReleaseBuilds".
subprojects {
    plugins.withId("com.android.application") {
        extensions.configure<com.android.build.api.dsl.ApplicationExtension> {
            lint {
                checkReleaseBuilds = false
            }
        }
    }
    plugins.withId("com.android.library") {
        extensions.configure<com.android.build.api.dsl.LibraryExtension> {
            lint {
                checkReleaseBuilds = false
            }
        }
    }
}

subprojects {
    project.evaluationDependsOn(":app")
}



tasks.register<Delete>("clean") {
    delete(rootProject.layout.buildDirectory)
}
