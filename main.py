import time

import aioble
import bluetooth
import esp32
import machine
import uasyncio as asyncio
from machine import ADC, Pin

try:
    import ujson as json
except ImportError:
    import json


# =========================================================
# CONFIGURATION PINS
# =========================================================
PIN_POT = 33
PIN_BTN_BRAKE = 13
PIN_BTN_HORN = 5
PIN_BTN_LIGHT = 2
PIN_BTN_FUEL = 19
PIN_BATTERY = 35
PIN_LED_ESP = 22

# =========================================================
# CONFIGURATION BLE LEGO
# =========================================================
LEGO_SVC_UUID = bluetooth.UUID("xxx")
LEGO_CHAR_UUID = bluetooth.UUID("xxx")

# Ports DUPLO
MOTOR_PORT = 0x00
SPEAKER_PORT = 0x01
LIGHT_PORT = 0x11

# Modes DUPLO
MOTOR_MODE = 0x00
SPEAKER_MODE = 0x01
LIGHT_MODE = 0x00

# =========================================================
# COULEURS ET SONS
# =========================================================
COLOR_OFF = 0x00
COLOR_RED = 0x09
COLOR_YELLOW = 0x07
COLOR_WHITE = 0x0A
COLOR_BLUE = 0x03
COLOR_PURPLE = 0x02
COLOR_GREEN = 0x06
COLOR_CYAN = 0x05

AVAILABLE_COLORS = [
    COLOR_WHITE,
    COLOR_RED,
    COLOR_GREEN,
    COLOR_BLUE,
    COLOR_YELLOW,
    COLOR_PURPLE,
    COLOR_CYAN
]

COLOR_NAMES = {
    0x00: "OFF",
    0x09: "ROUGE",
    0x07: "JAUNE",
    0x0A: "BLANC",
    0x03: "BLEU",
    0x02: "VIOLET",
    0x06: "VERT",
    0x05: "CYAN",
}

SOUND_BRAKE = 0x03
SOUND_DEPART = 0x05
SOUND_WATER = 0x07
SOUND_HORN = 0x09
SOUND_STEAM = 0x0A

# =========================================================
# CONFIGURATION POTENTIOMETRE
# =========================================================
POT_CONFIG_FILE = "pot_config.json"

# Valeurs par défaut raisonnables si aucun fichier n'existe encore
POT_DEFAULT_MIN_UV = 300000
POT_DEFAULT_MAX_UV = 2200000

# Comme on lit en microvolts, la "marge 150" est convertie ici
# en une marge fixe exploitable autour du centre.
POT_DEADZONE_MARGIN_UV = 90000

# Lecture moyennée pour calmer l'ADC de l'ESP32
POT_READ_SAMPLES = 16
POT_READ_DELAY_MS = 2

# =========================================================
# OPTIONS
# =========================================================
DEBUG_HARDWARE = False

# =========================================================
# VARIABLES D'ETAT
# =========================================================
connection = None
lego_char = None

current_speed_target = 0
motor_override = False
light_override = False
lights_on = False
current_color_idx = 0
buttons_locked = False
rainbow_mode = False
config_mode = False

speaker_ready = False
sound_busy = False
ble_tx_busy = False

pot_min_uv = POT_DEFAULT_MIN_UV
pot_max_uv = POT_DEFAULT_MAX_UV
pot_deadzone_low_uv = 0
pot_deadzone_high_uv = 0

last_user_action = time.ticks_ms()

# =========================================================
# INIT MATERIEL
# =========================================================
pot = ADC(Pin(PIN_POT))
pot.atten(ADC.ATTN_11DB)

btn_brake = Pin(PIN_BTN_BRAKE, Pin.IN, Pin.PULL_UP)
btn_horn = Pin(PIN_BTN_HORN, Pin.IN, Pin.PULL_UP)
btn_light = Pin(PIN_BTN_LIGHT, Pin.IN, Pin.PULL_UP)
btn_fuel = Pin(PIN_BTN_FUEL, Pin.IN, Pin.PULL_UP)

led_debug = Pin(PIN_LED_ESP, Pin.OUT)
led_debug.value(1)


# =========================================================
# OUTILS GENERAUX
# =========================================================
def reset_inactivity():
    global last_user_action
    last_user_action = time.ticks_ms()


def is_connected():
    global connection
    if not connection:
        return False
    try:
        return connection.is_connected()
    except Exception:
        return False


def all_buttons_pressed():
    return (
        btn_brake.value() == 0 and
        btn_horn.value() == 0 and
        btn_light.value() == 0 and
        btn_fuel.value() == 0
    )


async def wait_button_release(pin):
    await asyncio.sleep_ms(25)
    while pin.value() == 0:
        await asyncio.sleep_ms(25)
    await asyncio.sleep_ms(25)


# =========================================================
# CONFIG POTENTIOMETRE
# =========================================================
def recompute_deadzone():
    """
    Calcule la zone morte autour du milieu.
    Correction volontaire de la formule demandée :
    on prend le milieu entre min et max, puis +/- marge.
    """
    global pot_deadzone_low_uv, pot_deadzone_high_uv

    mid = (pot_min_uv + pot_max_uv) // 2
    pot_deadzone_low_uv = max(pot_min_uv, mid - POT_DEADZONE_MARGIN_UV)
    pot_deadzone_high_uv = min(pot_max_uv, mid + POT_DEADZONE_MARGIN_UV)

    if pot_deadzone_low_uv >= pot_deadzone_high_uv:
        pot_deadzone_low_uv = mid - 10000
        pot_deadzone_high_uv = mid + 10000


def load_pot_config():
    global pot_min_uv, pot_max_uv

    try:
        with open(POT_CONFIG_FILE, "r") as f:
            data = json.load(f)

        pot_min_uv = int(data.get("pot_min_uv", POT_DEFAULT_MIN_UV))
        pot_max_uv = int(data.get("pot_max_uv", POT_DEFAULT_MAX_UV))

        if pot_min_uv > pot_max_uv:
            pot_min_uv, pot_max_uv = pot_max_uv, pot_min_uv

        recompute_deadzone()

        print("SYS: Config potentiomètre chargée")
        print(
            "     min={}uV max={}uV deadzone=[{}uV..{}uV]".format(
                pot_min_uv, pot_max_uv, pot_deadzone_low_uv, pot_deadzone_high_uv
            )
        )

    except Exception:
        pot_min_uv = POT_DEFAULT_MIN_UV
        pot_max_uv = POT_DEFAULT_MAX_UV
        recompute_deadzone()

        print("SYS: Config potentiomètre par défaut")
        print(
            "     min={}uV max={}uV deadzone=[{}uV..{}uV]".format(
                pot_min_uv, pot_max_uv, pot_deadzone_low_uv, pot_deadzone_high_uv
            )
        )


def save_pot_config():
    try:
        data = {
            "pot_min_uv": pot_min_uv,
            "pot_max_uv": pot_max_uv,
            "pot_deadzone_low_uv": pot_deadzone_low_uv,
            "pot_deadzone_high_uv": pot_deadzone_high_uv,
            "margin_uv": POT_DEADZONE_MARGIN_UV,
        }

        with open(POT_CONFIG_FILE, "w") as f:
            json.dump(data, f)

        print("SYS: Config potentiomètre sauvegardée")
        return True

    except Exception as e:
        print("SYS: Erreur sauvegarde config pot:", e)
        return False


async def read_pot_uv_avg(samples=POT_READ_SAMPLES):
    total = 0
    for _ in range(samples):
        total += pot.read_uv()
        await asyncio.sleep_ms(POT_READ_DELAY_MS)
    return total // samples


# =========================================================
# CONSTRUCTION TRAMES LWP3
# =========================================================
def build_lwp3_msg(payload):
    return bytes([len(payload) + 2, 0x00] + payload)


def get_motor_cmd(speed):
    speed = max(-100, min(100, speed))
    val = speed & 0xFF
    return build_lwp3_msg([
        0x81,
        MOTOR_PORT,
        0x11,
        0x51,
        MOTOR_MODE,
        val
    ])


def get_sound_cmd(sound_id):
    return build_lwp3_msg([
        0x81,
        SPEAKER_PORT,
        0x11,
        0x51,
        SPEAKER_MODE,
        sound_id
    ])


def get_light_cmd(color_id):
    return build_lwp3_msg([
        0x81,
        LIGHT_PORT,
        0x11,
        0x51,
        LIGHT_MODE,
        color_id
    ])


def get_prepare_speaker_cmd():
    return bytes([0x0A, 0x00, 0x41, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x01])


# =========================================================
# ECRITURE BLE SERIE
# =========================================================
async def send_raw_cmd(cmd, post_delay_ms=50):
    global ble_tx_busy, speaker_ready

    if not (is_connected() and lego_char):
        return False

    waited = 0
    while ble_tx_busy:
        await asyncio.sleep_ms(5)
        waited += 5
        if waited >= 500:
            print("BLE: timeout attente TX")
            return False

    ble_tx_busy = True
    try:
        await lego_char.write(cmd, response=False)
        if post_delay_ms > 0:
            await asyncio.sleep_ms(post_delay_ms)
        return True
    except Exception as e:
        print("[BLE ERR]", e)
        speaker_ready = False
        return False
    finally:
        ble_tx_busy = False


async def set_motor(speed):
    return await send_raw_cmd(get_motor_cmd(speed), post_delay_ms=60)


async def set_light(color_id):
    return await send_raw_cmd(get_light_cmd(color_id), post_delay_ms=60)


async def init_speaker():
    global speaker_ready

    if not (is_connected() and lego_char):
        speaker_ready = False
        return False

    ok = await send_raw_cmd(get_prepare_speaker_cmd(), post_delay_ms=80)
    speaker_ready = ok

    if ok:
        print("SYS: Speaker initialisé")

    return ok


async def play_sound(sound_id, label="SON"):
    global sound_busy, speaker_ready

    if not (is_connected() and lego_char):
        return False

    if sound_busy:
        return False

    sound_busy = True
    try:
        if not speaker_ready:
            ok = await init_speaker()
            if not ok:
                return False

        print(">>> LECTURE SON : {} (0x{:02X})".format(label, sound_id))
        await asyncio.sleep_ms(20)

        ok = await send_raw_cmd(get_sound_cmd(sound_id), post_delay_ms=220)
        if ok:
            return True

        speaker_ready = False
        ok = await init_speaker()
        if not ok:
            return False

        await asyncio.sleep_ms(20)
        return await send_raw_cmd(get_sound_cmd(sound_id), post_delay_ms=220)

    finally:
        sound_busy = False


# =========================================================
# RESTAURATION / EFFETS
# =========================================================
async def restore_state():
    global lights_on, current_color_idx, current_speed_target, rainbow_mode

    target_color = AVAILABLE_COLORS[current_color_idx] if lights_on else COLOR_OFF

    if rainbow_mode:
        print(
            "--- RESTAURATION : Vitesse={} % | Lumière=ARC-EN-CIEL ---".format(
                current_speed_target
            )
        )
    else:
        print(
            "--- RESTAURATION : Vitesse={} % | Lumière={} ---".format(
                current_speed_target,
                COLOR_NAMES.get(target_color, "OFF")
            )
        )
        await set_light(target_color)

    await set_motor(current_speed_target)


async def flicker_light(color, duration_ms):
    end = time.ticks_add(time.ticks_ms(), duration_ms)

    while time.ticks_diff(end, time.ticks_ms()) > 0:
        if not light_override:
            break

        await set_light(color)
        await asyncio.sleep_ms(100)
        await set_light(COLOR_OFF)
        await asyncio.sleep_ms(50)


async def blink_white_done():
    for _ in range(10):
        await set_light(COLOR_WHITE)
        await asyncio.sleep_ms(120)
        await set_light(COLOR_OFF)
        await asyncio.sleep_ms(120)


# =========================================================
# MODE CONFIGURATION POTENTIOMETRE
# =========================================================
async def enter_pot_config_mode():
    global config_mode, motor_override, light_override, rainbow_mode
    global pot_min_uv, pot_max_uv

    config_mode = True
    motor_override = True
    light_override = True
    rainbow_mode = False
    reset_inactivity()

    print("")
    print("======================================")
    print(" MODE CONFIGURATION POTENTIOMETRE")
    print("======================================")

    await set_motor(0)
    await set_light(COLOR_WHITE)

    # On attend que le bouton frein soit relâché,
    # pour éviter les interactions parasites.
    while btn_brake.value() == 0:
        await asyncio.sleep_ms(50)

    print("ETAPE 1/2 : place le potentiomètre sur sa borne MAX, puis appuie sur KLAXON.")
    await set_light(COLOR_YELLOW)

    captured_max_uv = None
    while captured_max_uv is None:
        if not is_connected():
            print("SYS: Déconnexion pendant la configuration.")
            break

        if btn_horn.value() == 0:
            captured_max_uv = await read_pot_uv_avg()
            print("CONFIG: MAX enregistré = {} uV".format(captured_max_uv))
            await wait_button_release(btn_horn)
            break

        await asyncio.sleep_ms(20)

    if captured_max_uv is None:
        config_mode = False
        motor_override = False
        light_override = False
        await restore_state()
        return

    print("ETAPE 2/2 : place le potentiomètre sur sa borne MIN, puis appuie sur LUMIERE.")
    await set_light(COLOR_BLUE)

    captured_min_uv = None
    while captured_min_uv is None:
        if not is_connected():
            print("SYS: Déconnexion pendant la configuration.")
            break

        if btn_light.value() == 0:
            captured_min_uv = await read_pot_uv_avg()
            print("CONFIG: MIN enregistré = {} uV".format(captured_min_uv))
            await wait_button_release(btn_light)
            break

        await asyncio.sleep_ms(20)

    if captured_min_uv is None:
        config_mode = False
        motor_override = False
        light_override = False
        await restore_state()
        return

    # Sécurités : on remet dans le bon ordre au cas où
    pot_min_uv = min(captured_min_uv, captured_max_uv)
    pot_max_uv = max(captured_min_uv, captured_max_uv)

    if pot_max_uv - pot_min_uv < 200000:
        print("CONFIG: plage trop faible, configuration refusée.")
        await set_light(COLOR_RED)
        await asyncio.sleep_ms(1000)

        config_mode = False
        motor_override = False
        light_override = False
        await restore_state()
        return

    recompute_deadzone()
    save_pot_config()

    print("CONFIG OK:")
    print("  min={} uV".format(pot_min_uv))
    print("  max={} uV".format(pot_max_uv))
    print("  zone morte basse={} uV".format(pot_deadzone_low_uv))
    print("  zone morte haute={} uV".format(pot_deadzone_high_uv))

    await blink_white_done()

    config_mode = False
    motor_override = False
    light_override = False
    await restore_state()


# =========================================================
# BATTERIE
# =========================================================
def lire_batterie():
    try:
        bat = ADC(Pin(PIN_BATTERY))
        bat.atten(ADC.ATTN_11DB)

        total = 0
        for _ in range(16):
            total += bat.read()

        raw = total / 16
        volts = (raw / 4095) * 3.3 * 2 * 1.1
        pct = int(((volts - 3.3) / (4.2 - 3.3)) * 100)

        if pct < 0:
            pct = 0
        elif pct > 100:
            pct = 100

        print("SYS: Batterie {:.2f}V ({}%)".format(volts, pct))

    except Exception:
        pass


# =========================================================
# TACHES
# =========================================================
async def task_combo_lock():
    global buttons_locked

    print("SYS: Tâche Combo Lock démarrée")

    while True:
        if config_mode:
            await asyncio.sleep_ms(100)
            continue

        if all_buttons_pressed():
            buttons_locked = not buttons_locked
            etat = "DÉSACTIVÉS (POT SEUL)" if buttons_locked else "ACTIVÉS"
            print("\n>>> BASCULE MODE BOUTONS : {} <<<\n".format(etat))

            while all_buttons_pressed():
                await asyncio.sleep_ms(50)

            await asyncio.sleep_ms(250)

        await asyncio.sleep_ms(50)


async def task_rainbow():
    idx = 0

    while True:
        if rainbow_mode and not light_override and not config_mode and is_connected():
            await set_light(AVAILABLE_COLORS[idx])
            idx = (idx + 1) % len(AVAILABLE_COLORS)
            await asyncio.sleep_ms(400)
        else:
            await asyncio.sleep_ms(100)


async def task_inactivity_monitor():
    print("SYS: Moniteur d'inactivité globale démarré")

    while True:
        elapsed_mins = time.ticks_diff(time.ticks_ms(), last_user_action) / 60000

        if elapsed_mins >= 30 and not config_mode:
            print("SYS: 30 minutes d'inactivité totale. Mise en veille profonde.")
            await set_motor(0)
            await asyncio.sleep_ms(500)

            wake_pin = Pin(PIN_POT, Pin.IN)
            if wake_pin.value() == 1:
                esp32.wake_on_ext0(wake_pin, esp32.WAKEUP_ALL_LOW)
            else:
                esp32.wake_on_ext0(wake_pin, esp32.WAKEUP_ANY_HIGH)

            machine.deepsleep()

        await asyncio.sleep(10)


async def task_debug_hardware():
    print(
        "DEBUG MATERIEL ACTIF: Frein={}, Klaxon={}, Lum={}, Fuel={}".format(
            PIN_BTN_BRAKE, PIN_BTN_HORN, PIN_BTN_LIGHT, PIN_BTN_FUEL
        )
    )

    while True:
        b_brake = btn_brake.value()
        b_horn = btn_horn.value()
        b_light = btn_light.value()
        b_fuel = btn_fuel.value()

        if 0 in [b_brake, b_horn, b_light, b_fuel]:
            print(
                "INPUT -> Frein:{} | Klaxon:{} | Lum:{} | Fuel:{}".format(
                    b_brake, b_horn, b_light, b_fuel
                )
            )

        await asyncio.sleep_ms(200)


async def task_potentiometer():
    global current_speed_target, motor_override

    last_sent_speed = -999
    pot_locked = True
    start_uv = await read_pot_uv_avg()

    print("POT: Sécurité active. Valeur initiale: {} uV. Bougez pour activer.".format(start_uv))

    while True:
        if not motor_override and not config_mode and is_connected():
            raw_uv = await read_pot_uv_avg()

            if pot_locked:
                if abs(raw_uv - start_uv) > 120000:
                    pot_locked = False
                    reset_inactivity()
                    print("POT: DÉVERROUILLAGE MOTEUR !")
                else:
                    current_speed_target = 0
                    if last_sent_speed != 0:
                        await set_motor(0)
                        last_sent_speed = 0
                    await asyncio.sleep_ms(100)
                    continue

            target = 0

            if raw_uv < pot_deadzone_low_uv:
                clamped_uv = max(pot_min_uv, raw_uv)
                denom = pot_deadzone_low_uv - pot_min_uv

                if denom <= 0:
                    target = 0
                else:
                    ratio = (clamped_uv - pot_min_uv) / denom
                    raw_target = (ratio * 75) - 100
                    target = round(raw_target / 5) * 5

            elif raw_uv > pot_deadzone_high_uv:
                clamped_uv = min(pot_max_uv, raw_uv)
                denom = pot_max_uv - pot_deadzone_high_uv

                if denom <= 0:
                    target = 0
                else:
                    ratio = (clamped_uv - pot_deadzone_high_uv) / denom
                    raw_target = (ratio * 75) + 25
                    target = round(raw_target / 5) * 5

            else:
                target = 0

            current_speed_target = target

            if target != last_sent_speed:
                reset_inactivity()
                print("POT: Vitesse {}% ({} uV)".format(target, raw_uv))
                await set_motor(target)
                last_sent_speed = target

        await asyncio.sleep_ms(80)


async def task_buttons():
    global motor_override, light_override, lights_on
    global current_color_idx, rainbow_mode

    print("LOGIC: Tâche boutons démarrée")

    while True:
        if not is_connected():
            await asyncio.sleep_ms(200)
            continue

        if config_mode:
            await asyncio.sleep_ms(100)
            continue

        if buttons_locked:
            await asyncio.sleep_ms(50)
            continue

        if all_buttons_pressed():
            await asyncio.sleep_ms(50)
            continue

        # =================================================
        # FREIN (PIN 13)
        # - appui normal : freinage
        # - appui 10s : mode config potentiomètre
        # =================================================
        if btn_brake.value() == 0:
            reset_inactivity()
            print(">>> BTN {} (FREIN) ACTIVÉ <<<".format(PIN_BTN_BRAKE))

            motor_override = True
            light_override = True

            await set_motor(0)
            await set_light(COLOR_RED)
            await play_sound(SOUND_BRAKE, "FREIN")

            press_start = time.ticks_ms()
            entered_config = False

            while btn_brake.value() == 0 and not buttons_locked:
                if time.ticks_diff(time.ticks_ms(), press_start) >= 10000:
                    entered_config = True
                    await enter_pot_config_mode()
                    break

                await asyncio.sleep_ms(100)

            if not entered_config:
                motor_override = False
                light_override = False
                await restore_state()

            await asyncio.sleep_ms(250)

        # =================================================
        # LUMIERE (PIN 2)
        # =================================================
        elif btn_light.value() == 0:
            reset_inactivity()
            print(">>> BTN {} (LUMIERE) ACTIVÉ <<<".format(PIN_BTN_LIGHT))

            start = time.ticks_ms()

            while btn_light.value() == 0:
                if time.ticks_diff(time.ticks_ms(), start) > 800:
                    break
                await asyncio.sleep_ms(10)

            duration = time.ticks_diff(time.ticks_ms(), start)

            # Appui long : ON/OFF général lumières
            if duration > 800:
                rainbow_mode = False
                lights_on = not lights_on
                print("Lumières MASTER -> {}".format("ON" if lights_on else "OFF"))

                if lights_on:
                    await set_light(AVAILABLE_COLORS[current_color_idx])
                else:
                    await set_light(COLOR_OFF)

                while btn_light.value() == 0:
                    await asyncio.sleep_ms(50)

            else:
                # Attente relâchement du premier appui
                while btn_light.value() == 0:
                    await asyncio.sleep_ms(10)

                # Détection double appui
                double_tap = False
                wait_start = time.ticks_ms()

                while time.ticks_diff(time.ticks_ms(), wait_start) < 300:
                    if btn_light.value() == 0:
                        double_tap = True
                        break
                    await asyncio.sleep_ms(10)

                if double_tap:
                    if not lights_on:
                        rainbow_mode = not rainbow_mode
                        print("Action: Mode ARC-EN-CIEL {}".format(
                            "ACTIVÉ" if rainbow_mode else "DÉSACTIVÉ"
                        ))
                        if not rainbow_mode:
                            await restore_state()
                    else:
                        rainbow_mode = False
                        current_color_idx = (current_color_idx + 2) % len(AVAILABLE_COLORS)
                        col = AVAILABLE_COLORS[current_color_idx]
                        print("Action: Double saut couleur -> {}".format(COLOR_NAMES.get(col)))
                        await set_light(col)

                    while btn_light.value() == 0:
                        await asyncio.sleep_ms(50)

                else:
                    if lights_on:
                        rainbow_mode = False
                        current_color_idx = (current_color_idx + 1) % len(AVAILABLE_COLORS)
                        col = AVAILABLE_COLORS[current_color_idx]
                        print("Cycle couleur -> {}".format(COLOR_NAMES.get(col)))
                        await set_light(col)
                    else:
                        print("Ignoré (lumières éteintes)")

            await asyncio.sleep_ms(250)

        # =================================================
        # KLAXON / VAPEUR / DEPART (PIN 5)
        # =================================================
        elif btn_horn.value() == 0:
            reset_inactivity()
            print(">>> BTN {} (KLAXON) ACTIVÉ <<<".format(PIN_BTN_HORN))

            start = time.ticks_ms()
            light_override = True

            while btn_horn.value() == 0:
                if time.ticks_diff(time.ticks_ms(), start) > 600:
                    break
                await asyncio.sleep_ms(10)

            duration = time.ticks_diff(time.ticks_ms(), start)

            if duration > 600:
                print("Action: Vapeur (appui long)")
                await play_sound(SOUND_STEAM, "VAPEUR")
                await flicker_light(COLOR_WHITE, 2500)

                while btn_horn.value() == 0:
                    await asyncio.sleep_ms(50)

            else:
                while btn_horn.value() == 0:
                    await asyncio.sleep_ms(10)

                double_tap = False
                wait_start = time.ticks_ms()

                while time.ticks_diff(time.ticks_ms(), wait_start) < 300:
                    if btn_horn.value() == 0:
                        double_tap = True
                        break
                    await asyncio.sleep_ms(10)

                if double_tap:
                    print("Action: Départ station (double appui)")
                    await set_light(COLOR_GREEN)
                    await play_sound(SOUND_DEPART, "DEPART")
                    await asyncio.sleep_ms(1500)

                    while btn_horn.value() == 0:
                        await asyncio.sleep_ms(50)

                else:
                    print("Action: Klaxon (appui simple)")
                    await set_light(COLOR_YELLOW)
                    await play_sound(SOUND_HORN, "KLAXON")
                    await asyncio.sleep_ms(1000)

            light_override = False
            await restore_state()
            await asyncio.sleep_ms(250)

        # =================================================
        # FUEL / EAU (PIN 19)
        # =================================================
        elif btn_fuel.value() == 0:
            reset_inactivity()
            print(">>> BTN {} (FUEL) ACTIVÉ <<<".format(PIN_BTN_FUEL))

            motor_override = True
            light_override = True

            await set_motor(0)
            await set_light(COLOR_RED)
            await play_sound(SOUND_BRAKE, "FREIN")
            await asyncio.sleep_ms(700)

            if not buttons_locked and is_connected():
                await set_light(COLOR_BLUE)
                await play_sound(SOUND_WATER, "EAU")
                await flicker_light(COLOR_BLUE, 3500)

            while btn_fuel.value() == 0 and not buttons_locked:
                await asyncio.sleep_ms(50)

            motor_override = False
            light_override = False
            await restore_state()
            await asyncio.sleep_ms(250)

        await asyncio.sleep_ms(20)


async def connection_manager():
    global connection, lego_char, speaker_ready

    print("SYS: Manager connexion démarré")
    lire_batterie()

    disconnect_start = time.ticks_ms()

    while True:
        est_connecte = is_connected()

        if est_connecte:
            disconnect_start = None
            await asyncio.sleep(1)
            continue

        if disconnect_start is None:
            disconnect_start = time.ticks_ms()

        connection = None
        lego_char = None
        speaker_ready = False
        led_debug.value(1)

        elapsed_mins = time.ticks_diff(time.ticks_ms(), disconnect_start) / 60000

        if elapsed_mins >= 15:
            sleep_delay = 60
        elif elapsed_mins >= 5:
            sleep_delay = 30
        else:
            sleep_delay = 1

        print(
            "SYS: Scan Bluetooth... (Inactif depuis {:.1f} min | délai attente: {}s)".format(
                elapsed_mins, sleep_delay
            )
        )

        device = None
        try:
            async with aioble.scan(
                duration_ms=5000,
                interval_us=30000,
                window_us=30000,
                active=True
            ) as scanner:
                async for result in scanner:
                    try:
                        name = result.name() or ""
                    except Exception:
                        name = ""

                    try:
                        services = result.services()
                    except Exception:
                        services = []

                    if LEGO_SVC_UUID in services or name.lower() == "train":
                        device = result.device
                        print("SYS: Train détecté ({})".format(name))
                        break

        except Exception as e:
            print("SYS: Erreur durant le scan:", e)

        if device:
            try:
                print("SYS: Connexion à {}...".format(device))
                connection = await device.connect(timeout_ms=10000)

                service = await connection.service(LEGO_SVC_UUID)
                lego_char = await service.characteristic(LEGO_CHAR_UUID)

                print("SYS: CONNECTÉ AU TRAIN !")
                led_debug.value(0)

                await asyncio.sleep_ms(600)
                await init_speaker()
                await set_motor(0)
                await restore_state()

            except Exception as e:
                print("SYS: Erreur connexion:", e)

                if connection:
                    try:
                        await connection.disconnect()
                    except Exception:
                        pass

                connection = None
                lego_char = None
                speaker_ready = False

        else:
            await asyncio.sleep(sleep_delay)


# =========================================================
# MAIN
# =========================================================
async def main():
    load_pot_config()

    tasks = [
        connection_manager(),
        task_potentiometer(),
        task_buttons(),
        task_combo_lock(),
        task_rainbow(),
        task_inactivity_monitor(),
    ]

    if DEBUG_HARDWARE:
        tasks.append(task_debug_hardware())

    await asyncio.gather(*tasks)


try:
    asyncio.run(main())
finally:
    try:
        asyncio.new_event_loop()
    except Exception:
        pass