import pyautogui
import pyperclip
import time


class NIIMBOTController:

    def __init__(self):
        # เวลารอระหว่างคำสั่ง
        self.delay = 0.05

        # เปิด emergency stop ของ PyAutoGUI
        pyautogui.FAILSAFE = True

    def click(self, x, y):
        pyautogui.click(x, y)
        time.sleep(self.delay)


    def paste_unicode(self, text):
        pyperclip.copy(str(text))
        # time.sleep(0.1)

        pyautogui.hotkey("command", "v")
        # time.sleep(0.1)

    def write(self, text):
        pyautogui.write(str(text), interval=0.02)
        time.sleep(self.delay)

    def hotkey(self, *keys):
        pyautogui.hotkey(*keys)
        time.sleep(self.delay)

    def print_label(
        self,
        product,
        barcode,
        weight,
        price,
        lot,
        from_at,
        price_per_kg,
        types
    ):

        print("เริ่มสั่งพิมพ์...")
        old_position = pyautogui.position()
        
        # -------------------------
        # ตรงนี้จะใส่ตำแหน่งจริง
        # ของ NIIMBOT App
        # -------------------------

        # ตัวอย่าง:
        #
        self.click(1622, 665)
        self.click(1622, 665)
        time.sleep(0.1)

        
        self.click(1527, 439)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(product)
        
        self.click(1482, 479)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(price)
        
        self.click(1484, 517)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(price_per_kg)
        
        self.click(1486, 557)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(from_at)
        
        self.click(1484, 584)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(weight)
        
        self.click(1570, 626)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(lot)
        
        self.click(1487, 662)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(types)
        
        self.click(1531, 704)
        pyautogui.hotkey("command", "a")
        self.paste_unicode(barcode)
        
        self.click(1615, 353)
        self.click(1569, 389)
        time.sleep(1)
        self.click(1575, 785)
        time.sleep(1)
        self.click(1590, 755) #คลิกสั่งพิพม์
        time.sleep(4)
        self.click(1372, 324)

        print("ส่งคำสั่งพิมพ์แล้ว")

        pyautogui.moveTo(
            old_position.x,
            old_position.y,
            duration=0.1
        )


if __name__ == "__main__":

    printer = NIIMBOTController()

    printer.print_label(
        product="หมูบดเกรด A",
        barcode="31800902",
        price="20",
        weight = "0.200",
        lot="MFG:19/08/2026 18:37",
        from_at="BETAGRO",
        price_per_kg="97",
        types="🐷"
    )