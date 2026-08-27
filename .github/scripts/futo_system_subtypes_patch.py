from pathlib import Path

root = Path("futo")

method = root / "java/res/xml/method.xml"
method.write_text('''<?xml version="1.0" encoding="utf-8"?>

<input-method xmlns:android="http://schemas.android.com/apk/res/android"
    android:settingsActivity="org.futo.inputmethod.latin.uix.settings.SettingsActivity"
    android:isDefault="true"
    android:supportsSwitchingToNextInputMethod="true"
    android:supportsInlineSuggestions="true"
    android:suppressesSpellChecker="true">

    <subtype
        android:subtypeId="1001"
        android:icon="@drawable/ic_launcher_foreground"
        android:label="@string/futo_system_subtype_label"
        android:imeSubtypeLocale="en_US"
        android:languageTag="en-US"
        android:imeSubtypeMode="keyboard"
        android:imeSubtypeExtraValue="KeyboardLayoutSet=qwerty"
        android:isAsciiCapable="true" />

    <subtype
        android:subtypeId="1002"
        android:icon="@drawable/ic_launcher_foreground"
        android:label="@string/futo_system_subtype_label"
        android:imeSubtypeLocale="ko_KR"
        android:languageTag="ko-KR"
        android:imeSubtypeMode="keyboard"
        android:imeSubtypeExtraValue="KeyboardLayoutSet=korean_dubeolsik"
        android:isAsciiCapable="true" />
</input-method>
''', encoding="utf-8")

strings = root / "java/res/values/strings-appname.xml"
text = strings.read_text(encoding="utf-8")
if "futo_system_subtype_label" not in text:
    text = text.replace("</resources>", '    <string name="futo_system_subtype_label">%s</string>\n</resources>')
    strings.write_text(text, encoding="utf-8")

latin = root / "java/src/org/futo/inputmethod/latin/LatinIME.kt"
text = latin.read_text(encoding="utf-8")

if "import android.content.ComponentName\n" not in text:
    text = text.replace(
        "import android.content.BroadcastReceiver\n",
        "import android.content.BroadcastReceiver\nimport android.content.ComponentName\n",
        1,
    )

if "import android.view.inputmethod.InputMethodManager\n" not in text:
    text = text.replace(
        "import android.view.inputmethod.InputMethodSubtype\n",
        "import android.view.inputmethod.InputMethodManager\nimport android.view.inputmethod.InputMethodSubtype\n",
        1,
    )

if "import java.util.Locale\n" not in text:
    text = text.replace("import kotlin.math.roundToInt\n", "import java.util.Locale\nimport kotlin.math.roundToInt\n", 1)

bridge = r'''    private fun configuredSubtypeForLanguage(language: String): String? {
        return getSettingBlocking(SubtypesSetting).firstOrNull { subtypeString ->
            try {
                Subtypes.getLocale(Subtypes.convertToSubtype(subtypeString)).language == language
            } catch (_: Throwable) {
                false
            }
        }
    }

    private fun ensureConfiguredSubtypeForLanguage(language: String): String? {
        configuredSubtypeForLanguage(language)?.let { return it }

        when (language) {
            "en" -> Subtypes.addLanguage(this, Locale.US, "qwerty")
            "ko" -> Subtypes.addLanguage(this, Locale.KOREA, "korean_dubeolsik")
            else -> return null
        }

        return configuredSubtypeForLanguage(language)
    }

    private fun syncFutoLanguageFromSystemSubtype(newSubtype: InputMethodSubtype?) {
        if (newSubtype == null) return

        val locale = try {
            Subtypes.getLocale(newSubtype)
        } catch (_: Throwable) {
            return
        }

        if (locale.language != "en" && locale.language != "ko") return

        val target = ensureConfiguredSubtypeForLanguage(locale.language) ?: return

        // Persist FUTO's internal language selection asynchronously.  The immediate
        // changeSubtype() call below makes the switch visible without waiting for DataStore.
        launchJob {
            setSetting(ActiveSubtype, target)
        }
        changeSubtype(target)

        Log.i("LatinIME", "Android subtype ${newSubtype.locale} -> FUTO $target")
    }

    private fun enableEnglishAndKoreanSystemSubtypes() {
        if (Build.VERSION.SDK_INT < 34) return

        try {
            val imm = getSystemService(InputMethodManager::class.java)
            val imeId = ComponentName(this, LatinIME::class.java).flattenToShortString()

            // API 34+: enable our two statically-declared subtype IDs.  Reflection is
            // intentional here because the FUTO 0.1.30 build stubs expose a mismatched
            // Kotlin signature for this framework API even though the platform method
            // is setExplicitlyEnabledInputMethodSubtypes(String, int[]).
            val api = InputMethodManager::class.java.getMethod(
                "setExplicitlyEnabledInputMethodSubtypes",
                String::class.java,
                IntArray::class.java
            )
            api.invoke(imm, imeId, intArrayOf(1001, 1002))
            Log.i("LatinIME", "Enabled Android system subtypes en=1001 ko=1002 for $imeId")
        } catch (t: Throwable) {
            Log.e("LatinIME", "Failed to enable Android system IME subtypes", t)
        }
    }

    override fun onCurrentInputMethodSubtypeChanged(newSubtype: InputMethodSubtype?) {
        super.onCurrentInputMethodSubtypeChanged(newSubtype)
        syncFutoLanguageFromSystemSubtype(newSubtype)
    }

'''

on_create_anchor = "    override fun onCreate() {\n"
if "private fun enableEnglishAndKoreanSystemSubtypes()" not in text:
    if on_create_anchor not in text:
        raise SystemExit("LatinIME.onCreate anchor not found")
    text = text.replace(on_create_anchor, bridge + on_create_anchor, 1)

init_anchor = "        Subtypes.addDefaultSubtypesIfNecessary(this)\n"
if "        enableEnglishAndKoreanSystemSubtypes()\n" not in text:
    if init_anchor not in text:
        raise SystemExit("Subtypes init anchor not found")
    text = text.replace(init_anchor, init_anchor + "\n        enableEnglishAndKoreanSystemSubtypes()\n", 1)

latin.write_text(text, encoding="utf-8")

print("Patched Android IME system subtypes and FUTO language synchronization")
print(method.read_text(encoding="utf-8"))
