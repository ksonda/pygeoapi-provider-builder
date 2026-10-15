// Vocabulary suggestions for EDR parameters (Geoconnex options): common ODM2
// variable names and QUDT units for water and weather data. Every term was
// checked against vocabulary.odm2.org and qudt.org on 2026-10-15; the fields
// stay free text, so any other URI works too.

export const ODM2_VARIABLE = 'http://vocabulary.odm2.org/variablename/'
export const QUDT_UNIT = 'http://qudt.org/vocab/unit/'

/** ODM2 variable name terms and their labels. */
export const ODM2_VARIABLES: [string, string][] = [
  ['barometricPressure', 'Barometric pressure'],
  ['chlorophyll_a', 'Chlorophyll a'],
  ['discharge', 'Discharge'],
  ['evapotranspiration', 'Evapotranspiration'],
  ['gageHeight', 'Gage height'],
  ['groundwaterDepth', 'Groundwater depth'],
  ['nitrogenNitrate_NO3', 'Nitrogen, nitrate (NO3)'],
  ['oxygenDissolved', 'Oxygen, dissolved'],
  ['oxygenDissolvedPercentOfSaturation', 'Oxygen, dissolved percent of saturation'],
  ['pH', 'pH'],
  ['phosphorusTotal', 'Phosphorus, total'],
  ['precipitation', 'Precipitation'],
  ['relativeHumidity', 'Relative humidity'],
  ['reservoirStorage', 'Reservoir storage'],
  ['salinity', 'Salinity'],
  ['snowDepth', 'Snow depth'],
  ['snowWaterEquivalent', 'Snow water equivalent'],
  ['specificConductance', 'Specific conductance'],
  ['streamflow', 'Streamflow'],
  ['temperature', 'Temperature'],
  ['turbidity', 'Turbidity'],
  ['velocity', 'Velocity'],
  ['waterDepth', 'Water depth'],
  ['waterLevel', 'Water level'],
  ['windDirection', 'Wind direction'],
  ['windSpeed', 'Wind speed'],
]

/** QUDT unit codes, their labels, and unit symbols people type for them. */
export const QUDT_UNITS: [string, string, string[]][] = [
  ['AC-FT', 'acre-foot', ['ac-ft', 'acre-ft']],
  ['CentiM', 'centimetre', ['cm']],
  ['DEG', 'degree (angle)', ['deg', '°']],
  ['DEG_C', 'degree Celsius', ['degC', '°C', 'C']],
  ['DEG_F', 'degree Fahrenheit', ['degF', '°F', 'F']],
  ['FT', 'foot', ['ft']],
  ['FT-PER-SEC', 'foot per second', ['ft/s']],
  ['FT3-PER-SEC', 'cubic foot per second', ['ft3/s', 'cfs']],
  ['HectoPA', 'hectopascal', ['hPa']],
  ['IN', 'inch', ['in']],
  ['K', 'kelvin', ['K']],
  ['KiloPA', 'kilopascal', ['kPa']],
  ['L-PER-SEC', 'litre per second', ['L/s']],
  ['M', 'metre', ['m']],
  ['M-PER-SEC', 'metre per second', ['m/s']],
  ['M3', 'cubic metre', ['m3']],
  ['M3-PER-SEC', 'cubic metre per second', ['m3/s', 'cms']],
  ['MicroGM-PER-L', 'microgram per litre', ['ug/L', 'µg/L']],
  ['MicroS-PER-CentiM', 'microsiemens per centimetre', ['uS/cm', 'µS/cm']],
  ['MilliBAR', 'millibar', ['mbar', 'mb']],
  ['MilliGM-PER-L', 'milligram per litre', ['mg/L']],
  ['MilliM', 'millimetre', ['mm']],
  ['MilliM-PER-HR', 'millimetre per hour', ['mm/h', 'mm/hr']],
  ['NTU', 'nephelometric turbidity unit', ['NTU']],
  ['PERCENT', 'percent', ['%']],
  ['PH', 'pH', ['pH']],
  ['PPTH', 'parts per thousand', ['ppt', '‰']],
  ['W-PER-M2', 'watt per square metre', ['W/m2']],
]

/** The QUDT unit URI for a unit symbol, when the list knows the symbol. */
export function qudtForSymbol(symbol: string): string | null {
  const s = symbol.trim()
  if (!s) return null
  const hit = QUDT_UNITS.find(([, , symbols]) => symbols.includes(s)) ?? QUDT_UNITS.find(([, , symbols]) => symbols.some((x) => x.toLowerCase() === s.toLowerCase()))
  return hit ? `${QUDT_UNIT}${hit[0]}` : null
}
