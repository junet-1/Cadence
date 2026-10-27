"""Regionale Bauweise für route.py: Hausmodelle (blender/houses.py), Wand-/Dachfarben und Dachform je Weltregion und Klima.

Region kommt aus der Lage (Kontinent, grob über lon/lat-Rechtecke), Klima aus biome.py: Trockengebiete bauen überall flach,
kalte Bergdörfer in Europa wie in den Alpen. ponytail: Rechtecke statt Ländergrenzen; Grenzregionen (Texas/Mexiko, Türkei)
fallen mal auf die eine, mal auf die andere Seite. Natural-Earth-Polygone wenn das stört.
"""

WALLS_EU = [(.93, .91, .86), (.95, .90, .78), (.90, .85, .75), (.96, .94, .90), (.88, .78, .68), (.85, .85, .83), (.92, .86, .70)]
ROOFS_EU = [(.55, .22, .15), (.45, .18, .12), (.30, .30, .32), (.22, .22, .24), (.50, .30, .20)]
TERRACOTTA = [(.68, .34, .20), (.72, .42, .26), (.62, .30, .18)]
METAL = [(.55, .56, .58), (.62, .63, .64), (.50, .33, .24), (.45, .46, .48)]  # Wellblech, teils rostig
COLORFUL = [(.30, .66, .66), (.90, .55, .60), (.95, .80, .35), (.92, .55, .25), (.95, .95, .92), (.55, .75, .45), (.40, .55, .80)]

# houses: Modell -> Gewicht für normale Wohnhäuser; apartments: 3–4 Geschosse; small: < 40 m²; gable: prozedurale Häuser mit
# Satteldach (sonst Flachdach); church/barn: ob Kirche/Scheune als Modell passen
STYLES = {
    "mitteleuropa": dict(houses={"efh_sattel": .4, "efh_walm": .2, "siedlung": .25, "fachwerk": .15}, apartments="mfh",
                         walls=WALLS_EU, roofs=ROOFS_EU, gable=True, church=True, barn=True),
    "alpen": dict(houses={"chalet": .7, "efh_walm": .3}, apartments="mfh", walls=[(.95, .93, .88), (.92, .88, .80), (.90, .90, .88)],
                  roofs=[(.28, .28, .30), (.35, .25, .18), (.42, .40, .38)], gable=True, church=True, barn=True),
    "skandinavien": dict(houses={"holzhaus": .8, "efh_sattel": .2}, apartments="mfh",
                         walls=[(.55, .15, .12), (.60, .18, .14), (.86, .72, .38), (.93, .93, .90), (.45, .55, .62)],
                         roofs=[(.18, .18, .20), (.30, .30, .32), (.50, .20, .15)], gable=True, church=True, barn=True),
    "mittelmeer": dict(houses={"mediterran": .8, "flachdach": .2}, apartments="mediterran",
                       walls=[(.96, .95, .92), (.93, .85, .68), (.90, .78, .58), (.95, .90, .80), (.88, .70, .55)],
                       roofs=TERRACOTTA, gable=True, church=True, barn=False),
    "trocken": dict(houses={"flachdach": 1}, apartments="flachdach", small="flachdach",
                    walls=[(.94, .92, .87), (.90, .84, .72), (.86, .78, .64), (.96, .95, .93), (.84, .74, .60)],
                    roofs=[(.70, .66, .58)], gable=False, church=False, barn=False),
    "nordamerika": dict(houses={"suburban": .6, "ranch": .4}, apartments="mfh",
                        walls=[(.95, .95, .93), (.80, .82, .84), (.62, .70, .80), (.88, .82, .70), (.68, .74, .62), (.75, .45, .38)],
                        roofs=[(.30, .30, .32), (.40, .34, .30), (.20, .20, .22)], gable=True, church=True, barn=True),
    "lateinamerika": dict(houses={"beton": .6, "mediterran": .4}, apartments="beton", small="beton", walls=COLORFUL, roofs=TERRACOTTA,
                          gable=False, church=True, barn=False),
    "afrika": dict(houses={"wellblech": .65, "beton": .35}, apartments="beton", small="rundhuette",
                   walls=[(.78, .55, .38), (.85, .70, .52), (.92, .90, .85), (.70, .80, .78), (.88, .78, .55)],
                   roofs=METAL, gable=True, church=False, barn=False),
    "suedasien": dict(houses={"beton": .7, "flachdach": .3}, apartments="beton", small="beton", walls=COLORFUL, roofs=[(.60, .60, .58)],
                      gable=False, church=False, barn=False),
    "suedostasien": dict(houses={"tropenhaus": .6, "beton": .4}, apartments="beton", small="tropenhaus",
                         walls=[(.95, .93, .88), (.90, .85, .70), (.62, .45, .30), (.85, .90, .85), (.95, .85, .70)],
                         roofs=[(.75, .38, .20), (.62, .25, .18), (.30, .45, .65), (.55, .56, .58)], gable=True, church=False, barn=False),
    "ostasien": dict(houses={"ostasien": .8, "beton": .2}, apartments="block", walls=[(.93, .93, .91), (.82, .82, .80), (.88, .84, .76)],
                     roofs=[(.25, .27, .30), (.22, .27, .36), (.32, .32, .33)], gable=True, church=False, barn=False),
    "australien": dict(houses={"ranch": .8, "suburban": .2}, apartments="mfh",
                       walls=[(.62, .35, .25), (.70, .42, .30), (.93, .90, .82), (.96, .96, .94)],
                       roofs=[(.30, .32, .33), (.55, .25, .20), (.75, .75, .73), (.40, .45, .42)], gable=True, church=True, barn=True),
}


def region(lon, lat, t_mean, arid):
    """Name des Baustils für einen Ort. t_mean = Jahresmittel °C (biome.climate), arid = Trockenheit 0…1 (biome.aridity)."""
    if arid > .5:
        return "trocken"
    if -25 <= lon <= 45 and 35 <= lat <= 72:  # Europa
        if lat > 57 or (lon > 28 and lat > 50):
            return "skandinavien"
        if t_mean < 6.5 and lat < 49:
            return "alpen"  # kalte Bergdörfer (Alpen, Pyrenäen, Schwarzwald-Höhen)
        return "mittelmeer" if lat < 44.5 or t_mean > 13 else "mitteleuropa"
    if lon > 28 and lat > 50:
        return "skandinavien"  # Russland/Sibirien: Holzhäuser
    if -20 <= lon <= 60 and 15 <= lat < 37:
        return "mittelmeer" if lat > 30 else "trocken"  # Maghreb-/Levanteküste, sonst Sahara/Arabien
    if -20 <= lon <= 55 and lat < 15:
        return "afrika"
    if lon < -30:
        return "nordamerika" if lat > 32 or (lat > 24.5 and lon > -97.5) or lat < -60 else "lateinamerika"
    if 60 <= lon < 92 and lat < 37:
        return "suedasien"
    if lon >= 92 and -11 <= lat < 22:
        return "suedostasien"
    if 100 <= lon <= 150 and lat >= 22:
        return "ostasien"
    if lon >= 110 and lat < -11:
        return "australien"
    return "mitteleuropa"


def test(models):
    """models = Modellnamen aus route.MODELS; jede Region muss nur existierende Modelle nennen."""
    for name, s in STYLES.items():
        used = set(s["houses"]) | {s["apartments"], s.get("small", "garage")}
        assert used <= set(models), (name, used - set(models))
        assert abs(sum(s["houses"].values()) - 1) < 1e-9, name
    cases = [((8.68, 50.11, 10, 0), "mitteleuropa"), ((7.75, 46.02, 4, 0), "alpen"), ((10.75, 59.91, 6, 0), "skandinavien"),
             ((-5.98, 37.39, 18, .2), "mittelmeer"), ((8.13, 33.92, 20, .97), "trocken"), ((-104.99, 39.74, 10, .3), "nordamerika"),
             ((-80.19, 25.76, 24, 0), "nordamerika"), ((-99.13, 19.43, 16, .1), "lateinamerika"), ((36.82, -1.29, 19, .2), "afrika"),
             ((115.26, -8.51, 26, 0), "suedostasien"), ((77.21, 28.61, 25, .3), "suedasien"), ((139.69, 35.69, 15, 0), "ostasien"),
             ((151.21, -33.87, 18, .1), "australien"), ((37.62, 55.75, 5, 0), "skandinavien"), ((-58.38, -34.60, 17, 0), "lateinamerika")]
    for (lon, lat, t, a), want in cases:
        assert region(lon, lat, t, a) == want, (lon, lat, region(lon, lat, t, a), want)
    print("regions ok")
