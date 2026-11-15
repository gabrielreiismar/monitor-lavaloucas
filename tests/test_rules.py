import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lavaloucas.rules import Offer, detect_voltage, evaluate, is_new, looks_like_accessory
from lavaloucas.scrapers.base import parse_brl


def offer(**kw):
    base = dict(store="X", model_key="m", title="t", url="u", price=2400, voltage="220",
                new=True, in_stock=True, premium=False, reference_price=None)
    base.update(kw)
    return Offer(**base)


def ev(o):
    return evaluate(o, budget=2500, opp_cap=3300, opp_min_discount=0.25)


class Voltage(unittest.TestCase):
    def test_220(self):
        self.assertEqual(detect_voltage("Lava-louças Midea 14 Serviços 220V Preta"), "220")
        self.assertEqual(detect_voltage("Voltagem: 220 volts"), "220")
        self.assertEqual(detect_voltage("", "Voltagem 220V"), "220")

    def test_127(self):
        self.assertEqual(detect_voltage("Lava-louças 14 serviços 127V"), "127")
        self.assertEqual(detect_voltage("110V"), "127")

    def test_ambiguous_is_unknown(self):
        self.assertIsNone(detect_voltage("disponível em 127V e 220V"))
        self.assertIsNone(detect_voltage("Lava-louças 14 serviços"))

    def test_specific_beats_generic(self):
        # variação selecionada diz 127V, título diz 220V -> manda a variação
        self.assertEqual(detect_voltage("127V", "Lava-louças 220V"), "127")

    def test_bivolt(self):
        self.assertEqual(detect_voltage("Bivolt automático"), "bivolt")

    def test_not_fooled_by_other_numbers(self):
        self.assertIsNone(detect_voltage("Modelo 2200 watts 14 serviços"))


class Condition(unittest.TestCase):
    def test_new(self):
        self.assertTrue(is_new("Lava-louças Midea DWA14", "novo"))

    def test_not_new(self):
        for t in ("Lava-louças Open Box", "Lava-louças Usada", "Lava Louças Recondicionado",
                  "Lava louças vitrine", "Lava-louças com avaria"):
            self.assertFalse(is_new(t), t)
        self.assertFalse(is_new("Lava-louças", "usado"))


class Tiers(unittest.TestCase):
    def test_meta(self):
        self.assertEqual(ev(offer(price=2400)).tier, "META")

    def test_127_blocked_even_if_cheap(self):
        self.assertIsNone(ev(offer(price=1900, voltage="127")).tier)

    def test_unknown_voltage_blocked(self):
        self.assertIsNone(ev(offer(price=1900, voltage=None)).tier)

    def test_used_blocked(self):
        self.assertIsNone(ev(offer(price=1900, new=False)).tier)

    def test_no_stock_blocked(self):
        self.assertIsNone(ev(offer(price=1900, in_stock=False)).tier)

    def test_premium_opportunity(self):
        v = ev(offer(price=3000, premium=True, reference_price=4300))
        self.assertEqual(v.tier, "OPORTUNIDADE")

    def test_premium_fake_discount_blocked(self):
        self.assertIsNone(ev(offer(price=3000, premium=True, reference_price=3300)).tier)

    def test_premium_over_cap(self):
        self.assertIsNone(ev(offer(price=3600, premium=True, reference_price=5500)).tier)

    def test_non_premium_over_budget(self):
        self.assertIsNone(ev(offer(price=3000, premium=False, reference_price=4300)).tier)

    def test_premium_without_history_alerts_under_cap(self):
        self.assertEqual(ev(offer(price=3100, premium=True)).tier, "OPORTUNIDADE")


class Misc(unittest.TestCase):
    def test_brl(self):
        self.assertEqual(parse_brl("R$ 2.499,90"), 2499.9)
        self.assertEqual(parse_brl("3.299"), 3299.0)

    def test_accessory(self):
        self.assertTrue(looks_like_accessory("Pastilha para lava-louças", 59, 900))
        self.assertFalse(looks_like_accessory("Lava-louças 14 serviços", 2500, 900))



class Finish(unittest.TestCase):
    def test_detect(self):
        from lavaloucas.rules import detect_finish
        self.assertEqual(detect_finish("Lava-louças 14 Serviços Cor Inox"), "inox")
        self.assertEqual(detect_finish("Samsung 14 serviços Black Inox 220V"), "inox")
        self.assertEqual(detect_finish("Midea 14 Serviços Preta"), "outra")
        self.assertEqual(detect_finish("LAVA-LOUCAS MIDEA 14 SERVICOS CINZA 220V"), "outra")
        self.assertIsNone(detect_finish("Lava-louças Midea 14 serviços"))

    def test_evaluate_inox(self):
        kw = dict(budget=2500, opp_cap=3300, opp_min_discount=0.25, require_inox=True)
        self.assertIsNone(evaluate(offer(price=2000, finish="outra"), **kw).tier)
        self.assertEqual(evaluate(offer(price=2000, finish="inox"), **kw).tier, "META")
        self.assertEqual(evaluate(offer(price=2000, finish=None), **kw).tier, "META")
        self.assertIsNone(evaluate(offer(price=2000, finish=None), inox_unknown_ok=False, **kw).tier)


class Features(unittest.TestCase):
    def test_detect(self):
        from lavaloucas.rules import detect_features
        t = "Ciclo rápido 30 min, Smart Sensor, Meia carga, função Higienizar"
        self.assertEqual(detect_features(t), {"ciclo_rapido", "sensor_sujeira", "meia_carga", "higienizacao"})
        self.assertEqual(detect_features("Lava-louças 14 serviços inox"), set())
        self.assertIn("sensor_sujeira", detect_features("Detecta o nível de sujeira da louça"))
        self.assertIn("higienizacao", detect_features("programa Sanitize"))

    def test_evaluate(self):
        req = ("ciclo_rapido", "sensor_sujeira", "meia_carga", "higienizacao")
        kw = dict(budget=2500, opp_cap=3300, opp_min_discount=0.25, required_features=req)
        full = {"ciclo_rapido", "sensor_sujeira", "meia_carga", "higienizacao"}
        self.assertEqual(evaluate(offer(price=2000, features=full), **kw).tier, "META")
        v = evaluate(offer(price=2000, features={"ciclo_rapido"}), **kw)
        self.assertIsNone(v.tier); self.assertIn("sensor_sujeira", v.reason)
        # página não lida -> não bloqueia (evita falso negativo)
        self.assertEqual(evaluate(offer(price=2000, features=None), **kw).tier, "META")


if __name__ == "__main__":
    unittest.main()
