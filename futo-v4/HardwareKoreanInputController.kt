package org.futo.inputmethod.latin

import android.view.KeyEvent
import android.view.inputmethod.InputConnection

/**
 * Handles Korean input from a physical QWERTY keyboard without depending on
 * FUTO's on-screen keyboard subtype or Android's disabled legacy hardware path.
 *
 * The controller intentionally avoids composing spans. It keeps the raw
 * Dubeolsik jamo sequence locally, replaces the previously committed syllable
 * string in a batch edit, and commits the recomposed result as ordinary text.
 */
internal class HardwareKoreanInputController {
    enum class Result {
        NOT_HANDLED,
        HANDLED,
        SWITCHED_TO_KOREAN,
        SWITCHED_TO_ENGLISH,
    }

    private var koreanMode = false
    private val rawJamo = StringBuilder()
    private var renderedText = ""
    private val consumedKeyCodes = mutableSetOf<Int>()

    fun setKoreanMode(enabled: Boolean) {
        if (koreanMode != enabled) {
            resetTracking()
            koreanMode = enabled
        }
    }

    fun resetTracking() {
        rawJamo.setLength(0)
        renderedText = ""
    }

    fun onKeyDown(keyCode: Int, event: KeyEvent?, inputConnection: InputConnection?): Result {
        if (event == null) return Result.NOT_HANDLED

        if (isLanguageSwitch(keyCode, event)) {
            consumedKeyCodes.add(keyCode)
            if (event.repeatCount == 0) {
                resetTracking()
                koreanMode = !koreanMode
                return if (koreanMode) Result.SWITCHED_TO_KOREAN else Result.SWITCHED_TO_ENGLISH
            }
            return Result.HANDLED
        }

        if (!koreanMode) return Result.NOT_HANDLED

        // Preserve application shortcuts such as Ctrl+C, Alt+Left and Meta+V.
        if (event.isCtrlPressed || event.isAltPressed || event.isMetaPressed) {
            resetTracking()
            return Result.NOT_HANDLED
        }

        if (keyCode == KeyEvent.KEYCODE_DEL) {
            if (inputConnection != null && deleteLastJamo(inputConnection)) {
                consumedKeyCodes.add(keyCode)
                return Result.HANDLED
            }
            resetTracking()
            return Result.NOT_HANDLED
        }

        val jamo = mapDubeolsikKey(keyCode, event.isShiftPressed)
        if (jamo != null && inputConnection != null) {
            if (appendJamo(inputConnection, jamo)) {
                consumedKeyCodes.add(keyCode)
                return Result.HANDLED
            }
            resetTracking()
            return Result.NOT_HANDLED
        }

        // Pressing Shift itself must not terminate the current Hangul sequence.
        if (!isModifierKey(keyCode)) {
            resetTracking()
        }
        return Result.NOT_HANDLED
    }

    fun onKeyUp(keyCode: Int, event: KeyEvent?): Boolean {
        if (consumedKeyCodes.remove(keyCode)) return true
        return event != null && isLanguageSwitch(keyCode, event)
    }

    private fun isLanguageSwitch(keyCode: Int, event: KeyEvent): Boolean {
        return keyCode == KeyEvent.KEYCODE_LANGUAGE_SWITCH ||
            (keyCode == KeyEvent.KEYCODE_SPACE && event.isCtrlPressed) ||
            // Linux input scan code KEY_HANGEUL. Some Android keyboards expose
            // this scan code while reporting KEYCODE_UNKNOWN.
            event.scanCode == 122
    }

    private fun isModifierKey(keyCode: Int): Boolean = when (keyCode) {
        KeyEvent.KEYCODE_SHIFT_LEFT,
        KeyEvent.KEYCODE_SHIFT_RIGHT,
        KeyEvent.KEYCODE_CTRL_LEFT,
        KeyEvent.KEYCODE_CTRL_RIGHT,
        KeyEvent.KEYCODE_ALT_LEFT,
        KeyEvent.KEYCODE_ALT_RIGHT,
        KeyEvent.KEYCODE_META_LEFT,
        KeyEvent.KEYCODE_META_RIGHT,
        KeyEvent.KEYCODE_CAPS_LOCK,
        KeyEvent.KEYCODE_NUM_LOCK,
        KeyEvent.KEYCODE_SCROLL_LOCK -> true
        else -> false
    }

    private fun appendJamo(inputConnection: InputConnection, jamo: Char): Boolean {
        // If the cursor moved or the target application rewrote text, abandon the
        // old local segment instead of deleting unrelated text.
        if (!renderedTextMatchesBeforeCursor(inputConnection)) {
            resetTracking()
        }

        inputConnection.beginBatchEdit()
        return try {
            if (renderedText.isNotEmpty() && !deleteBeforeCursor(inputConnection, renderedText.length)) {
                return false
            }

            rawJamo.append(jamo)
            val nextText = composeHangul(rawJamo)
            if (!inputConnection.commitText(nextText, 1)) {
                return false
            }
            renderedText = nextText
            true
        } finally {
            inputConnection.endBatchEdit()
        }
    }

    private fun deleteLastJamo(inputConnection: InputConnection): Boolean {
        if (rawJamo.isEmpty() || renderedText.isEmpty()) return false
        if (!renderedTextMatchesBeforeCursor(inputConnection)) {
            resetTracking()
            return false
        }

        inputConnection.beginBatchEdit()
        return try {
            if (!deleteBeforeCursor(inputConnection, renderedText.length)) return false

            rawJamo.deleteCharAt(rawJamo.lastIndex)
            if (rawJamo.isEmpty()) {
                renderedText = ""
                true
            } else {
                val nextText = composeHangul(rawJamo)
                if (!inputConnection.commitText(nextText, 1)) return false
                renderedText = nextText
                true
            }
        } finally {
            inputConnection.endBatchEdit()
        }
    }

    private fun renderedTextMatchesBeforeCursor(inputConnection: InputConnection): Boolean {
        if (renderedText.isEmpty()) return true
        return inputConnection.getTextBeforeCursor(renderedText.length, 0)?.toString() == renderedText
    }

    private fun deleteBeforeCursor(inputConnection: InputConnection, length: Int): Boolean {
        if (length <= 0) return true
        return inputConnection.deleteSurroundingTextInCodePoints(length, 0) ||
            inputConnection.deleteSurroundingText(length, 0)
    }

    private fun mapDubeolsikKey(keyCode: Int, shifted: Boolean): Char? = when (keyCode) {
        KeyEvent.KEYCODE_Q -> if (shifted) 'ㅃ' else 'ㅂ'
        KeyEvent.KEYCODE_W -> if (shifted) 'ㅉ' else 'ㅈ'
        KeyEvent.KEYCODE_E -> if (shifted) 'ㄸ' else 'ㄷ'
        KeyEvent.KEYCODE_R -> if (shifted) 'ㄲ' else 'ㄱ'
        KeyEvent.KEYCODE_T -> if (shifted) 'ㅆ' else 'ㅅ'
        KeyEvent.KEYCODE_Y -> 'ㅛ'
        KeyEvent.KEYCODE_U -> 'ㅕ'
        KeyEvent.KEYCODE_I -> 'ㅑ'
        KeyEvent.KEYCODE_O -> if (shifted) 'ㅒ' else 'ㅐ'
        KeyEvent.KEYCODE_P -> if (shifted) 'ㅖ' else 'ㅔ'
        KeyEvent.KEYCODE_A -> 'ㅁ'
        KeyEvent.KEYCODE_S -> 'ㄴ'
        KeyEvent.KEYCODE_D -> 'ㅇ'
        KeyEvent.KEYCODE_F -> 'ㄹ'
        KeyEvent.KEYCODE_G -> 'ㅎ'
        KeyEvent.KEYCODE_H -> 'ㅗ'
        KeyEvent.KEYCODE_J -> 'ㅓ'
        KeyEvent.KEYCODE_K -> 'ㅏ'
        KeyEvent.KEYCODE_L -> 'ㅣ'
        KeyEvent.KEYCODE_Z -> 'ㅋ'
        KeyEvent.KEYCODE_X -> 'ㅌ'
        KeyEvent.KEYCODE_C -> 'ㅊ'
        KeyEvent.KEYCODE_V -> 'ㅍ'
        KeyEvent.KEYCODE_B -> 'ㅠ'
        KeyEvent.KEYCODE_N -> 'ㅜ'
        KeyEvent.KEYCODE_M -> 'ㅡ'
        else -> null
    }

    private data class Cluster(val first: Char, val second: Char)

    private val initials = listOf(
        'ㄱ', 'ㄲ', 'ㄴ', 'ㄷ', 'ㄸ', 'ㄹ', 'ㅁ', 'ㅂ', 'ㅃ', 'ㅅ',
        'ㅆ', 'ㅇ', 'ㅈ', 'ㅉ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'
    )

    private val finals: List<Char?> = listOf(
        null, 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ',
        'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ', 'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ',
        'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ'
    )

    private val mergedClusters = mapOf(
        Cluster('ㄱ', 'ㅅ') to 'ㄳ',
        Cluster('ㄴ', 'ㅈ') to 'ㄵ',
        Cluster('ㄴ', 'ㅎ') to 'ㄶ',
        Cluster('ㄹ', 'ㄱ') to 'ㄺ',
        Cluster('ㄹ', 'ㅁ') to 'ㄻ',
        Cluster('ㄹ', 'ㅂ') to 'ㄼ',
        Cluster('ㄹ', 'ㅅ') to 'ㄽ',
        Cluster('ㄹ', 'ㅌ') to 'ㄾ',
        Cluster('ㄹ', 'ㅍ') to 'ㄿ',
        Cluster('ㄹ', 'ㅎ') to 'ㅀ',
        Cluster('ㅂ', 'ㅅ') to 'ㅄ',
        Cluster('ㅗ', 'ㅏ') to 'ㅘ',
        Cluster('ㅗ', 'ㅐ') to 'ㅙ',
        Cluster('ㅗ', 'ㅣ') to 'ㅚ',
        Cluster('ㅜ', 'ㅓ') to 'ㅝ',
        Cluster('ㅜ', 'ㅔ') to 'ㅞ',
        Cluster('ㅜ', 'ㅣ') to 'ㅟ',
        Cluster('ㅡ', 'ㅣ') to 'ㅢ',
    )

    private val splitClusters = mergedClusters.entries.associate { (pair, merged) -> merged to pair }

    private fun isInitial(char: Char): Boolean = initials.contains(char)
    private fun isVowel(char: Char): Boolean = char.code in 0x314F..0x3163
    private fun isFinal(char: Char): Boolean = finals.indexOf(char) > 0

    private fun toBlock(initial: Char, vowel: Char, final: Char?): Char {
        val initialIndex = initials.indexOf(initial)
        val vowelIndex = vowel.code - 0x314F
        val finalIndex = finals.indexOf(final)
        check(initialIndex >= 0 && vowelIndex in 0 until 21 && finalIndex >= 0)
        return (0xAC00 + initialIndex * 21 * 28 + vowelIndex * 28 + finalIndex).toChar()
    }

    private fun composeHangul(input: CharSequence): String {
        var initial: Char? = null
        var vowel: Char? = null
        var final: Char? = null
        val result = StringBuilder()

        for (char in input) {
            if (initial == null) {
                if (isInitial(char)) initial = char else result.append(char)
                continue
            }

            if (vowel == null) {
                if (isVowel(char)) {
                    vowel = char
                } else {
                    result.append(initial)
                    if (isInitial(char)) initial = char else {
                        initial = null
                        result.append(char)
                    }
                }
                continue
            }

            if (final == null) {
                if (isVowel(char)) {
                    val merged = mergedClusters[Cluster(vowel, char)]
                    if (merged != null) {
                        vowel = merged
                    } else {
                        result.append(toBlock(initial, vowel, null))
                        initial = null
                        vowel = null
                        result.append(char)
                    }
                    continue
                }

                if (isInitial(char)) {
                    if (isFinal(char)) {
                        final = char
                    } else {
                        result.append(toBlock(initial, vowel, null))
                        initial = char
                        vowel = null
                    }
                    continue
                }

                result.append(toBlock(initial, vowel, null))
                initial = null
                vowel = null
                result.append(char)
                continue
            }

            if (isInitial(char)) {
                val merged = mergedClusters[Cluster(final, char)]
                if (merged != null && isFinal(merged)) {
                    final = merged
                } else {
                    result.append(toBlock(initial, vowel, final))
                    initial = char
                    vowel = null
                    final = null
                }
                continue
            }

            if (isVowel(char)) {
                val split = splitClusters[final]
                if (split != null && isFinal(split.first)) {
                    final = split.first
                    result.append(toBlock(initial, vowel, final))
                    initial = split.second
                    vowel = char
                    final = null
                } else {
                    val nextInitial = final
                    final = null
                    result.append(toBlock(initial, vowel, null))
                    initial = nextInitial
                    vowel = char
                }
                continue
            }

            result.append(toBlock(initial, vowel, final))
            initial = null
            vowel = null
            final = null
            result.append(char)
        }

        if (initial != null) {
            if (vowel != null) {
                result.append(toBlock(initial, vowel, final))
            } else {
                result.append(initial)
                if (final != null) result.append(final)
            }
        }

        return result.toString()
    }
}
