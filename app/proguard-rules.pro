# youtubedl-android bundles Jackson for its --dump-json parsing
# (mapper.VideoInfo/VideoFormat/etc reflect on field names + @JsonProperty).
# The library's own module-level proguard-rules.pro is NOT exported via
# consumerProguardFiles, so without these rules our own R8 minification
# would silently strip/rename the very fields Jackson binds by reflection.
-keepattributes *Annotation*,Signature,EnclosingMethod
-keep class com.yausername.** { *; }
-keepclassmembers class com.yausername.youtubedl_android.mapper.** { *; }
-dontwarn com.fasterxml.jackson.databind.**
-keep class com.fasterxml.jackson.databind.ObjectMapper { *; }
-keepclassmembers class com.fasterxml.jackson.** { *; }

-keep class com.saimum.viddown.engine.** { *; }

# Gson model classes (DownloadOption tiers, etc.)
-keep class com.saimum.viddown.data.model.** { *; }

# BUG FIX: CustomShortcut is Gson-serialized (SettingsRepository.addCustomShortcut/
# removeCustomShortcut) but actually lives in com.saimum.viddown.data, one
# package above .data.model -- the rule above never matched it. In a real
# minified release build (isMinifyEnabled = true), R8 was free to rename/strip
# its `label`/`url` fields, which Gson binds by name, so custom browser
# shortcuts could silently fail to save/load correctly (or throw) in the
# actual release APK even though everything looked fine in a debug build.
-keep class com.saimum.viddown.data.CustomShortcut { *; }

# Room
-keep class * extends androidx.room.RoomDatabase
-dontwarn androidx.room.paging.**
