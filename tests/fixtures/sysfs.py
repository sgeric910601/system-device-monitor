"""假的 /sys/class/power_supply：各廠牌筆電電池與變壓器的合成資料。

單位與核心相同：能量 µWh、電量 µAh、電壓 µV、電流 µA、功率 µW、溫度 0.1 °C。
名稱與型號都是虛構的；序號檔的內容也是假的，只用來確認讀取器從不打開它。
每個產生器的關鍵字參數會覆寫電池屬性，值為 None 代表不建立該屬性檔。
"""
from pathlib import Path

SUPPLY_DIR = "sys/class/power_supply"
FAKE_SERIAL = "FAKE-SERIAL-0001"


def supply(root, name, **attributes):
    """建立一個 power_supply 項目；值為 None 的屬性不建立。"""
    directory = Path(root)/SUPPLY_DIR/name
    directory.mkdir(parents=True, exist_ok=True)
    for key, value in attributes.items():
        if value is not None:
            (directory/key).write_text(f"{value}\n")
    return directory


def merged(base, overrides):
    return {**base, **overrides}


def thinkpad(root, online=0, **overrides):
    """能量制（energy_*）、power_now、起點與上限兩個門檻、Mains 變壓器。"""
    battery = dict(
        type="Battery", status="Discharging", present=1, technology="Li-poly",
        manufacturer="Example Cells", model_name="Example 45N1", capacity=82,
        capacity_level="Normal", energy_now=41_000_000, energy_full=50_000_000,
        energy_full_design=55_000_000, power_now=15_000_000, voltage_now=12_450_000,
        voltage_min_design=11_550_000, cycle_count=212, temp=314,
        charge_control_start_threshold=75, charge_control_end_threshold=80,
        charge_behaviour="[auto] inhibit-charge force-discharge", serial_number=FAKE_SERIAL)
    supply(root, "BAT0", **merged(battery, overrides))
    supply(root, "AC", type="Mains", online=online)


def asus(root, online=1, **overrides):
    """電量制（charge_*）、充電時 current_now 為負值、只有上限門檻。"""
    battery = dict(
        type="Battery", status="Charging", present=1, technology="Li-ion",
        manufacturer="Example Power", model_name="Example A32", capacity=64,
        capacity_level="Normal", charge_now=2_688_000, charge_full=4_200_000,
        charge_full_design=4_800_000, current_now=-2_100_000, voltage_now=15_600_000,
        voltage_min_design=15_400_000, cycle_count=0, charge_control_end_threshold=80,
        serial_number=FAKE_SERIAL)
    supply(root, "BAT1", **merged(battery, overrides))
    supply(root, "AC0", type="Mains", online=online)


def framework(root, **overrides):
    """USB-C PD（ucsi）變壓器有即時電壓電流與額定值；電池為電量制。"""
    battery = dict(
        type="Battery", status="Charging", present=1, technology="Li-ion",
        manufacturer="Example", model_name="Example FB01", capacity=55,
        charge_now=1_965_000, charge_full=3_572_000, charge_full_design=3_915_000,
        current_now=1_500_000, voltage_now=16_800_000, voltage_min_design=15_480_000,
        cycle_count=87, charge_control_end_threshold=90, serial_number=FAKE_SERIAL)
    supply(root, "BAT1", **merged(battery, overrides))
    supply(root, "ACAD", type="Mains", online=1)
    supply(root, "ucsi-source-psy-USBC000:001", type="USB", online=1, scope="Unknown",
           usb_type="C [PD] PD_PPS", voltage_now=20_000_000, current_now=2_250_000,
           voltage_max=20_000_000, current_max=3_250_000)
    supply(root, "ucsi-source-psy-USBC000:002", type="USB", online=0, scope="Unknown",
           usb_type="[C] PD PD_PPS", voltage_now=5_000_000, current_now=0,
           voltage_max=5_000_000, current_max=3_000_000)


def no_thresholds(root, **overrides):
    """沒有任何充電門檻檔的機型（例如名稱為 CMB0 的電池）。"""
    battery = dict(
        type="Battery", status="Discharging", present=1, capacity=47,
        energy_now=23_500_000, energy_full=50_000_000, energy_full_design=52_000_000,
        power_now=9_000_000, voltage_now=11_400_000, cycle_count=40)
    supply(root, "CMB0", **merged(battery, overrides))
    supply(root, "ADP1", type="Mains", online=0)


def not_charging_at_limit(root, **overrides):
    """接著電源、電量停在上限 80%，核心回報 Not charging。"""
    battery = dict(
        type="Battery", status="Not charging", present=1, capacity=80,
        energy_now=40_000_000, energy_full=50_000_000, energy_full_design=55_000_000,
        power_now=0, voltage_now=12_900_000, charge_control_end_threshold=80)
    supply(root, "BATT", **merged(battery, overrides))
    supply(root, "AC", type="Mains", online=1)


def unknown_status(root, **overrides):
    """核心回報 Unknown 狀態（例如 Apple 晶片的 macsmc-battery）。"""
    battery = dict(
        type="Battery", status="Unknown", present=1, capacity=91,
        energy_now=45_500_000, energy_full=50_000_000, power_now=300_000,
        voltage_now=12_800_000)
    supply(root, "macsmc-battery", **merged(battery, overrides))
    supply(root, "macsmc-ac", type="Mains", online=1)


def two_batteries(root, online=0):
    """兩顆電池：BAT0 放電中、BAT1 閒置。"""
    supply(root, "BAT0", type="Battery", status="Discharging", present=1, capacity=60,
           energy_now=14_400_000, energy_full=24_000_000, energy_full_design=24_000_000,
           power_now=8_000_000, voltage_now=11_800_000, cycle_count=120, temp=300,
           charge_control_end_threshold=85, charge_control_start_threshold=70)
    supply(root, "BAT1", type="Battery", status="Unknown", present=1, capacity=90,
           energy_now=43_200_000, energy_full=48_000_000, energy_full_design=50_000_000,
           power_now=0, voltage_now=12_200_000, cycle_count=30, temp=320,
           charge_control_end_threshold=85, charge_control_start_threshold=70)
    supply(root, "AC", type="Mains", online=online)


def peripheral(root, name="hidpp_battery_0"):
    """無線滑鼠等周邊電池：scope 為 Device，絕不能當成筆電電池。"""
    supply(root, name, type="Battery", scope="Device", status="Discharging", capacity=55,
           online=1, model_name="Example Mouse", serial_number=FAKE_SERIAL)


def garbage(root):
    """各種壞值：非數字、負的滿充容量、250% 電量、NaN 電壓、負的循環次數。"""
    supply(root, "BAT0", type="Battery", status="Discharging\x07", present=1,
           capacity=250, energy_now="abc", energy_full=-5, energy_full_design="",
           power_now="nan", voltage_now="12x", cycle_count=-1, temp=9999,
           charge_control_end_threshold=150, charge_control_start_threshold="on",
           manufacturer="Bad\x01Vendor" + "x" * 200)
    supply(root, "AC", type="Mains", online="maybe")


def absent_slot(root):
    """ThinkPad 第二電池槽沒有裝電池（present 為 0）。"""
    supply(root, "BAT1", type="Battery", present=0, status="Unknown")
