from pathlib import Path
import xml.etree.ElementTree as ET

root = Path("futo")

method = root / "java/res/xml/method.xml"
method.write_text(
    '''<?xml version="1.0" encoding="utf-8"?>

<input-method xmlns:android="http://schemas.android.com/apk/res/android"
    android:settingsActivity="org.futo.inputmethod.latin.uix.settings.SettingsActivity"
    android:isDefault="true"
    android:supportsSwitchingToNextInputMethod="true"
    android:supportsInlineSuggestions="true"
    android:suppressesSpellChecker="true">

    <subtype
        android:icon="@drawable/ic_launcher_foreground"
        android:label="@string/futo_system_subtype_label"
        android:imeSubtypeLocale="en_US"
        android:languageTag="en-US"
        android:imeSubtypeMode="keyboard"
        android:imeSubtypeExtraValue="KeyboardLayoutSet=qwerty"
        android:isAsciiCapable="true"
        android:subtypeId="130101" />

    <subtype
        android:icon="@drawable/ic_launcher_foreground"
        android:label="@string/futo_system_subtype_label"
        android:imeSubtypeLocale="ko_KR"
        android:languageTag="ko-KR"
        android:imeSubtypeMode="keyboard"
        android:imeSubtypeExtraValue="KeyboardLayoutSet=korean_dubeolsik"
        android:isAsciiCapable="true"
        android:subtypeId="130102" />
</input-method>
''',
    encoding="utf-8",
)

strings = root / "java/res/values/strings-appname.xml"
text = strings.read_text(encoding="utf-8")
if "futo_system_subtype_label" not in text:
    text = text.replace(
        "</resources>",
        '    <!-- Android replaces %s with the subtype locale display name. -->\n'
        '    <string name="futo_system_subtype_label">%s</string>\n'
        "</resources>",
    )
    strings.write_text(text, encoding="utf-8")

latin = root / "java/src/org/futo/inputmethod/latin/LatinIME.kt"
text = latin.read_text(encoding="utf-8")


def add_import(after: str, new_imports: str) -> None:
    global text
    if new_imports.splitlines()[0] in text:
        return
    if after not in text:
        raise SystemExit(f"Import anchor not found: {after!r}")
    text = text.replace(after, after + new_imports, 1)


add_import(
    "import android.view.inputmethod.InputConnection\n",
    "import android.view.inputmethod.InputMethodInfo\n"
    "import android.view.inputmethod.InputMethodManager\n",
)
add_import(
    "import org.futo.inputmethod.latin.uix.setSetting\n",
    "import org.futo.inputmethod.latin.uix.setSettingBlocking\n",
)
add_import(
    "import org.futo.inputmethod.v2keyboard.isFoldableInnerDisplayAllowed\n",
    "import java.util.Locale\n",
)

bridge_marker = "private var systemSubtypeBridgeReady = false"
bridge_anchor = "    private var unlockReceiver = UnlockedBroadcastReceiver { onDeviceUnlocked() }\n"
bridge = r'''
    // Android-visible English/Korean subtypes are kept in sync with FUTO's
    // internal ActiveSubtype. This is required for hardware-keyboard language
    // shortcuts such as Ctrl+Space to behave like Gboard's subtype switching.
    private var systemSubtypeBridgeReady = false
    private var pendingSystemSubtypeHash: Int? = null

    private fun ownInputMethodInfo(imm: InputMethodManager): InputMethodInfo? {
        return imm.inputMethodList.firstOrNull {
            it.packageName == packageName && it.serviceName == LatinIME::class.java.name
        } ?: imm.inputMethodList.firstOrNull {
            it.packageName == packageName
        }
    }

    private fun systemSubtypeLanguage(subtype: InputMethodSubtype?): String? {
        if (subtype == null) return null
        return try {
            Subtypes.getLocale(subtype).language
        } catch (_: Throwable) {
            null
        }
    }

    private fun futoSubtypeLanguage(subtypeString: String): String? {
        return try {
            Subtypes.getLocale(Subtypes.convertToSubtype(subtypeString)).language
        } catch (_: Throwable) {
            null
        }
    }

    private fun futoSubtypeCountry(subtypeString: String): String? {
        return try {
            Subtypes.getLocale(Subtypes.convertToSubtype(subtypeString)).country
        } catch (_: Throwable) {
            null
        }
    }

    private fun futoSubtypeLayout(subtypeString: String): String? {
        return try {
            Subtypes.convertToSubtype(subtypeString)
                .getExtraValueOf("KeyboardLayoutSet")
        } catch (_: Throwable) {
            null
        }
    }

    private fun configuredSubtypeForLanguage(language: String): String? {
        val candidates = getSettingBlocking(SubtypesSetting)
            .filter { futoSubtypeLanguage(it) == language }
            .sorted()

        val preferredCountry = when (language) {
            "en" -> "US"
            "ko" -> "KR"
            else -> ""
        }
        val preferredLayout = when (language) {
            "en" -> "qwerty"
            "ko" -> "korean_dubeolsik"
            else -> ""
        }

        return candidates.firstOrNull {
            futoSubtypeCountry(it) == preferredCountry &&
                    futoSubtypeLayout(it) == preferredLayout
        } ?: candidates.firstOrNull {
            futoSubtypeLayout(it) == preferredLayout
        } ?: candidates.firstOrNull()
    }

    private fun ensureConfiguredSubtypeForLanguage(language: String): String? {
        configuredSubtypeForLanguage(language)?.let { return it }
        if (!isDirectBootUnlocked) return null

        when (language) {
            "en" -> Subtypes.addLanguage(this, Locale.US, "qwerty")
            "ko" -> Subtypes.addLanguage(this, Locale.KOREA, "korean_dubeolsik")
            else -> return null
        }

        return configuredSubtypeForLanguage(language)
    }

    private fun systemSubtypeForLanguage(
        imm: InputMethodManager,
        language: String
    ): Pair<InputMethodInfo, InputMethodSubtype>? {
        val self = ownInputMethodInfo(imm) ?: return null
        val subtype = (0 until self.subtypeCount)
            .asSequence()
            .map { self.getSubtypeAt(it) }
            .firstOrNull { systemSubtypeLanguage(it) == language }
            ?: return null

        return self to subtype
    }

    private fun syncFutoLanguageFromSystemSubtype(newSubtype: InputMethodSubtype?) {
        if (!systemSubtypeBridgeReady) return

        val language = systemSubtypeLanguage(newSubtype) ?: return
        if (language != "en" && language != "ko") return

        if (newSubtype != null && pendingSystemSubtypeHash == newSubtype.hashCode()) {
            pendingSystemSubtypeHash = null
        }

        val target = ensureConfiguredSubtypeForLanguage(language) ?: return
        if (getSettingBlocking(ActiveSubtype) != target) {
            setSettingBlocking(ActiveSubtype.key, target)
        }

        changeSubtype(target)
        Log.i("LatinIME", "Android subtype ${newSubtype?.locale} -> FUTO $target")
    }

    private fun syncSystemSubtypeFromFutoSubtype(futoSubtype: InputMethodSubtype) {
        if (!systemSubtypeBridgeReady) return

        val language = systemSubtypeLanguage(futoSubtype) ?: return
        if (language != "en" && language != "ko") return

        try {
            val imm = getSystemService(InputMethodManager::class.java)
            val (self, target) = systemSubtypeForLanguage(imm, language) ?: return
            val targetHash = target.hashCode()
            val currentHash = imm.currentInputMethodSubtype?.hashCode()

            if (currentHash == targetHash) {
                pendingSystemSubtypeHash = null
                return
            }
            if (pendingSystemSubtypeHash == targetHash) return

            pendingSystemSubtypeHash = targetHash
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                switchInputMethod(self.id, target)
            } else {
                @Suppress("DEPRECATION")
                val switched = imm.setCurrentInputMethodSubtype(target)
                if (!switched) pendingSystemSubtypeHash = null
            }

            Log.i("LatinIME", "FUTO ${futoSubtype.locale} -> Android subtype ${target.locale}")
        } catch (t: Throwable) {
            pendingSystemSubtypeHash = null
            Log.e("LatinIME", "Failed to synchronize Android IME subtype", t)
        }
    }

    private fun enableEnglishAndKoreanSystemSubtypes() {
        if (!isDirectBootUnlocked) return

        try {
            ensureConfiguredSubtypeForLanguage("en") ?: return
            ensureConfiguredSubtypeForLanguage("ko") ?: return

            val imm = getSystemService(InputMethodManager::class.java)
            val self = ownInputMethodInfo(imm) ?: return
            val allSubtypes = (0 until self.subtypeCount)
                .map { self.getSubtypeAt(it) }
            val english = allSubtypes.firstOrNull {
                systemSubtypeLanguage(it) == "en"
            }
            val korean = allSubtypes.firstOrNull {
                systemSubtypeLanguage(it) == "ko"
            }

            if (english == null || korean == null) {
                Log.e(
                    "LatinIME",
                    "Static English/Korean subtypes missing: " +
                            allSubtypes.joinToString { "${it.locale}:${it.hashCode()}" }
                )
                return
            }

            val wanted = listOf(english, korean)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                imm.setExplicitlyEnabledInputMethodSubtypes(
                    self.id,
                    wanted.map { it.hashCode() }.toIntArray()
                )
            }

            systemSubtypeBridgeReady = true

            val activeSubtype = Subtypes.getActiveSubtype(this)
            val activeLanguage = systemSubtypeLanguage(activeSubtype)
            if (activeLanguage == "en" || activeLanguage == "ko") {
                syncSystemSubtypeFromFutoSubtype(activeSubtype)
            } else {
                syncFutoLanguageFromSystemSubtype(imm.currentInputMethodSubtype)
            }

            Log.i(
                "LatinIME",
                "Android system subtypes enabled: " +
                        wanted.joinToString { "${it.locale}:${it.hashCode()}" }
            )
        } catch (t: Throwable) {
            systemSubtypeBridgeReady = false
            pendingSystemSubtypeHash = null
            Log.e("LatinIME", "Failed to enable Android English/Korean subtypes", t)
        }
    }

    override fun onCurrentInputMethodSubtypeChanged(newSubtype: InputMethodSubtype?) {
        super.onCurrentInputMethodSubtypeChanged(newSubtype)
        syncFutoLanguageFromSystemSubtype(newSubtype)
    }

'''

if bridge_marker not in text:
    class_index = text.find("class LatinIME : InputMethodServiceCompose()")
    anchor_index = text.find(bridge_anchor)
    if class_index < 0 or anchor_index < 0 or anchor_index < class_index:
        raise SystemExit("LatinIME bridge anchor not found inside LatinIME")
    text = text.replace(bridge_anchor, bridge_anchor + bridge, 1)

init_anchor = "        latinIMELegacy.onCreate()\n"
init_call = "        enableEnglishAndKoreanSystemSubtypes()\n"
if init_call not in text:
    if init_anchor not in text:
        raise SystemExit("LatinIME initialization anchor not found")
    text = text.replace(init_anchor, init_anchor + "\n" + init_call, 1)

old_change = '''    fun changeSubtype(subtypeString: String) {
        if(currentSubtype == subtypeString) return
        currentSubtype = subtypeString

        val subtype = Subtypes.convertToSubtype(subtypeString)
        changeInputMethodSubtype(subtype)
        uixManager.updateLocale(Subtypes.getLocale(subtype))
    }
'''
new_change = '''    fun changeSubtype(subtypeString: String) {
        val subtype = Subtypes.convertToSubtype(subtypeString)

        if(currentSubtype != subtypeString) {
            currentSubtype = subtypeString
            changeInputMethodSubtype(subtype)
            uixManager.updateLocale(Subtypes.getLocale(subtype))
        }

        syncSystemSubtypeFromFutoSubtype(subtype)
    }
'''
if old_change in text:
    text = text.replace(old_change, new_change, 1)
elif "syncSystemSubtypeFromFutoSubtype(subtype)" not in text:
    raise SystemExit("LatinIME.changeSubtype anchor not found")

unlock_anchor = "        CanThrowIfDebug = true\n"
unlock_call = "        enableEnglishAndKoreanSystemSubtypes()\n"
unlock_start = text.find("    private fun onDeviceUnlocked()")
unlock_section = text[unlock_start:] if unlock_start >= 0 else ""
if unlock_call not in unlock_section:
    anchor_index = text.find(unlock_anchor, unlock_start)
    if unlock_start < 0 or anchor_index < 0:
        raise SystemExit("onDeviceUnlocked anchor not found")
    insert_at = anchor_index + len(unlock_anchor)
    text = text[:insert_at] + "\n" + unlock_call + text[insert_at:]

latin.write_text(text, encoding="utf-8")

# Fail the build harness immediately if the patch landed in the wrong class or
# the two Android-visible subtypes are not exactly what we expect.
patched = latin.read_text(encoding="utf-8")
latin_class = patched.index("class LatinIME : InputMethodServiceCompose()")
bridge_index = patched.index(bridge_marker)
base_class_on_create = patched.index("open class InputMethodServiceCompose")
assert bridge_index > latin_class > base_class_on_create
assert patched.index(init_call, bridge_index) > bridge_index
assert "syncSystemSubtypeFromFutoSubtype(subtype)" in patched
assert patched.count("override fun onCurrentInputMethodSubtypeChanged") == 1

android_ns = "{http://schemas.android.com/apk/res/android}"
xml_root = ET.parse(method).getroot()
subtypes = list(xml_root.findall("subtype"))
assert len(subtypes) == 2
assert [s.attrib[android_ns + "imeSubtypeLocale"] for s in subtypes] == ["en_US", "ko_KR"]
assert [s.attrib[android_ns + "subtypeId"] for s in subtypes] == ["130101", "130102"]
assert all(android_ns + "overridesImplicitlyEnabledSubtype" not in s.attrib for s in subtypes)

print("Patched and verified Android-visible English/Korean IME subtypes")
print(method.read_text(encoding="utf-8"))
