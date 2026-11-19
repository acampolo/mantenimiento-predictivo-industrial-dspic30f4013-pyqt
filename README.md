# Nodo Edge Computing para Mantenimiento Predictivo de Motores DC - dsPIC30F4013 y Python

![MCU](https://img.shields.io/badge/MCU-dsPIC30F4013-red?style=for-the-badge&logo=microchip)
![C](https://img.shields.io/badge/Firmware-C_XC16-blue?style=for-the-badge&logo=c)
![Python](https://img.shields.io/badge/HMI-Python_3.10+-yellow?style=for-the-badge&logo=python)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-lightgrey?style=for-the-badge)
![Modbus](https://img.shields.io/badge/Bus-Modbus_RTU_RS--485-green?style=for-the-badge)
![scikit-learn](https://img.shields.io/badge/ML-Isolation_Forest-orange?style=for-the-badge&logo=scikitlearn)

> **Nodo embebido que mide corriente y vibración de un motor de corriente continua, procesa las señales en el propio dsPIC y las envía por Modbus RTU a una interfaz en Python que estima el estado de salud y la vida útil restante del motor.**

Este proyecto nació en el curso Integrador I de Ingeniería Electrónica de la UTP. La idea es que el nodo no dependa de la nube para lo importante. El dsPIC calcula el valor eficaz de la corriente, el espectro de vibración y la velocidad real del eje, y además corta la alimentación del motor ante una sobrecorriente con un comparador analógico, sin pasar por el software. La PC se queda con lo que sí conviene hacer allí, que es graficar, guardar el historial y correr el modelo de aprendizaje automático.

---

## 📋 Tabla de Contenidos
1. [Características Principales](#-características-principales)
2. [Arquitectura General](#-arquitectura-general)
3. [Hardware](#-hardware)
4. [Firmware del dsPIC](#-firmware-del-dspic)
5. [Mapa de Registros Modbus](#-mapa-de-registros-modbus)
6. [HMI en Python](#-hmi-en-python)
7. [Estructura del Repositorio](#-estructura-del-repositorio)
8. [Instalación y Uso](#-instalación-y-uso)
9. [Resultados](#-resultados)
10. [Limitaciones](#-limitaciones)
11. [Autor](#-autor)

---

## 🚀 Características Principales

- **Protección por hardware**: Un comparador LMV331 vigila la señal del sensor de corriente y dispara la interrupción externa INT0. La rutina de servicio apaga el PWM del motor sin esperar al bucle principal.
- **Muestreo adaptativo**: La frecuencia de muestreo se calcula a partir de las RPM del motor, a razón de 16 muestras por vuelta del rotor y limitada entre 250 Hz y 5 kHz. Así el espectro siempre cubre los armónicos que interesan.
- **Procesamiento por lotes**: Se capturan 64 muestras por canal en la interrupción del ADC y recién cuando el lote está completo se calcula el True RMS, la FFT y la velocidad real del eje.
- **Rango dual de ganancia**: Cada canal analógico se lee con ganancia 1 y con ganancia 11. Si la lectura amplificada se satura, el firmware reconstruye la señal con la de ganancia 1.
- **RPM medidas desde la corriente**: Un filtro pasa altas sobre la corriente deja ver el rizado de las delgas del colector. Contando esos pulsos y conociendo el número de delgas se obtiene la velocidad real sin sensor extra.
- **Recuperación ante reinicios**: La configuración vive en variables persistentes y se valida con una bandera. Si el reinicio fue por una caída de voltaje o por el pin MCLR, el firmware descarta la configuración y pide una nueva.
- **Modbus RTU esclavo**: Pila NanoModbus sobre UART2 a 115200 baudios con control de dirección RS-485.
- **HMI con diagnóstico**: Interfaz de varias pestañas con envolvente de Hilbert, cepstrum, verificación de Parseval, detección de anomalías con Isolation Forest, índice de degradación y estimación de vida útil remanente.
- **Historial local**: Base de datos SQLite con sesiones, muestras, firmas de arranque y eventos, más exportación a Excel.

---

## 🧭 Arquitectura General

```
 Sensor Hall ACS758 ──┐                                   ┌── Pantalla LCD 4x20 por I2C
                      ├──► Acondicionamiento ──► dsPIC30F4013 ──┤
 Piezoeléctrico ──────┘     analógico                │      └── PWM al MOSFET del motor
                                  │                   │
                          Comparador LMV331 ──► INT0 ─┘   (corte por hardware)
                                                      │
                                              UART2 + RS-485
                                                      │
                                              Modbus RTU esclavo
                                                      │
                                    PC: hilo Modbus ─► HMI PyQt5 ─► SQLite / Excel
                                                         └─► Isolation Forest, RUL
```

| Bloque | Dónde corre | Qué hace |
| :--- | :--- | :--- |
| Adquisición y lotes | dsPIC, interrupción del ADC | Muestrea corriente y vibración, cuenta pulsos de delgas. |
| DSP | dsPIC, bucle principal | True RMS, filtro EMA, ventana de Hanning, FFT de 64 puntos. |
| Seguridad | Hardware más INT0 | Corte del PWM ante sobrecorriente. |
| Comunicación | dsPIC y PC | Modbus RTU a 115200 baudios sobre RS-485. |
| Análisis avanzado | PC | Hilbert, cepstrum, Kalman, Isolation Forest, RUL. |

---

## 🔧 Hardware

| Elemento | Componente | Para qué lo usé |
| :--- | :--- | :--- |
| Procesador | dsPIC30F4013 | Control y DSP. Cristal de 7.3728 MHz con PLL 16x, lo que da un FCY de 29.4912 MHz. |
| Sensor de corriente | ACS758 de 50 A | Medición de corriente por efecto Hall con aislamiento. |
| Sensor de vibración | Transductor piezoeléctrico | Captura de oscilaciones de la carcasa con un ancho de banda mucho mayor al de un MEMS. |
| Amplificación | MCP6002 | Ganancia de 11 sobre la corriente y filtro antialiasing pasivo. |
| Protección | LMV331 con histéresis | Comparador con umbral ajustable por potenciómetro. |
| Etapa de potencia | MOSFET AOD4184A con driver Totem-Pole | Conmutación del motor, aislada con un optoacoplador 6N137. |
| Protección inductiva | Diodo MBR2045CT y TVS 1.5KE33A | Circulación libre y recorte de sobretensiones. |
| Red industrial | Transceptor RS-485 | Enlace diferencial con la PC. |
| Alertas remotas | Módulo celular Luat Air780e | Aviso por 4G cuando se cae el enlace local. |
| Visualización local | LCD 4x20 con PCF8574 | Estado, RPM, corriente y vibración. |
| Encapsulado | Caja impresa en PETG | Protección contra el polvo de las escobillas. |

La PCB es de cuatro capas con topología de tierra en estrella y una zona libre de cobre bajo el aislamiento del bus RS-485.

### Asignación de pines que usé

| Función | Pin |
| :--- | :--- |
| Actuador de emergencia | RC13 |
| LED de estado | RB3 |
| LED de actividad del ADC | RB9 |
| LED de transmisión celular | RB10 |
| LED de alarma | RB8 |
| Botón de prueba | RD9 |
| SCL del I2C por software | RB12 |
| SDA del I2C por software | RB11 |
| Control de dirección RS-485 | RD1 |

---

## 🧠 Firmware del dsPIC

### Reloj y protecciones

El oscilador es externo en modo XT con PLL 16x. El perro guardián está activo con prescalers de 1:512 y 1:16, lo que da una ventana de unos 16 segundos. El reset por baja tensión está en 2.7 V y el temporizador de arranque en 64 ms.

### Máquina de estados

| Estado | Duración | Qué pasa |
| :--- | :--- | :--- |
| Espera de RPM | Hasta que llegue un valor válido por Modbus | El motor permanece parado y la LCD pide las RPM. |
| Banda muerta | 3 s | Se ignora el arranque para no contaminar la estadística. |
| Calibración | De los 3 s a los 10 s | Se promedia la corriente filtrada para fijar la referencia del motor sano. |
| Monitoreo | Continuo | Se calculan las FFT de corriente y vibración y se actualizan los registros. |

Las RPM válidas van de 10 a 10000. Cuando llegan, se guardan en variables persistentes junto con las delgas y la relación de reducción.

### Cálculo del True RMS y filtro EMA

El offset del ADC de 12 bits se resta, se acumulan los cuadrados de 64 muestras, se divide con un desplazamiento y se saca la raíz con un algoritmo de enteros. Después se convierte a miliamperios y se suaviza.

```c
uint32_t media_cuadratica = suma_cuadrados >> 6;
uint16_t true_rms_crudo   = (uint16_t)integer_sqrt(media_cuadratica);
uint32_t true_rms_ma      = ((uint32_t)true_rms_crudo * MULT_ACS758) / 1000UL;

if (true_rms_ma < 65) { true_rms_ma = 0; }

ema_corriente += ((int32_t)true_rms_ma - ema_corriente) >> 3;
```

La constante `MULT_ACS758` vale 2774 y está calibrada para el modelo de 50 A. Si se usa otro, hay que cambiarla.

### FFT en el motor DSP

Cada canal se acondiciona así antes de la transformada: se quita la componente continua, se aplica la ventana de Hanning, se amplifica por 16 para aprovechar el formato Q15 y se satura por seguridad. El arreglo de trabajo va en memoria Y con alineación forzada, porque el módulo DSP genera un error de dirección si no la tiene.

```c
signal_val = (int32_t)fuente[i] - offset_dc;
signal_val = (signal_val * (int32_t)windowHanning[i]) >> 15;
signal_val = signal_val << 4;

FFTComplexIP(6, workspace_fft, (fractcomplex*)twiddleFactors, (int)__builtin_psvpage(&twiddleFactors));
BitReverseComplex(6, workspace_fft);
SquareMagnitudeCplx(32, workspace_fft, (fractional*)destino_modbus);
```

El pico espectral se busca desde el bin 2, para dejar fuera la componente continua y el ruido de muy baja frecuencia, y se descarta si su magnitud es menor a 5.

### Velocidad real por rizado de delgas

En la interrupción del ADC, la corriente pasa por un filtro pasa altas de primer orden con coeficiente 31130 sobre 32768. Cada flanco positivo que supera el umbral cuenta como un pulso de delga. Al completar el lote, las RPM se obtienen así:

```c
rpm_calculado = (picos * fs * 60) / (64 * num_delgas);
```

Si la configuración trae cero delgas, el firmware usa 3 para evitar una división por cero.

### Muestreo dinámico

La frecuencia de muestreo se calcula con las RPM del rotor, que ya incluyen la caja reductora, y se programa en el Timer 3 eligiendo el prescaler que deje el período dentro de 16 bits.

```c
f_muestreo = (rpm_rotor_interno * 16UL) / 60UL;   // limitada entre 250 y 5000 Hz
temp_pr3   = (29491200UL / f_muestreo) - 1UL;
```

Como el lote es de 64 muestras, la resolución espectral es fs sobre 64. Con un motor a 1800 RPM, por ejemplo, la fs resulta de 480 Hz y cada bin vale 7.5 Hz.

### Autorrango de ganancia

```c
if (raw_i_x11 > 4090 || raw_i_x11 < 5)
{
    ac_reconstruida = ((int32_t)raw_i_x1 - 2048) * 11;
    raw_i_final     = clamp(ac_reconstruida + 2048, 0, 4095);
}
else
{
    raw_i_final = raw_i_x11;
}
```

### Protección y trampas de error

| Evento | Respuesta |
| :--- | :--- |
| INT0 por comparador | Fija el PWM en parada, activa la bandera de sobrecorriente y sube el pin del actuador. |
| Error de dirección o de pila | Reinicio inmediato del procesador. |
| Error matemático | Limpia los acumuladores A y B del DSP y sigue. |
| Cualquier alerta activa o registro 141 en 1 | Parada de emergencia y LED de alarma. |
| Reset desde el SCADA sin alertas activas | Vuelve a la espera de RPM. |

Los acumuladores DSP trabajan con saturación activada para que la FFT no se desborde.

### Modbus y I2C

La capa Modbus usa NanoModbus como pila, con un búfer circular de recepción de 64 bytes llenado desde la interrupción de UART2 y un temporizador de 1 ms como base de tiempo. Los callbacks copian los registros byte por byte, porque el búfer de la pila puede quedar en una dirección impar y una copia por palabra provocaría un error de dirección en este procesador.

El I2C de la pantalla es por software, con las líneas en colector abierto simulado sobre RB11 y RB12 a unos 100 kHz.

---

## 📡 Mapa de Registros Modbus

La dirección del esclavo es 1 y los registros son de retención de 16 bits.

| Dirección | Contenido | Sentido |
| :--- | :--- | :--- |
| 0 | RPM objetivo | Escritura desde la PC |
| 1 | Corriente filtrada en mA | Lectura |
| 2 | Vibración filtrada | Lectura |
| 3 | Byte de alertas | Lectura |
| 4 a 35 | Espectro de la corriente, 32 bins | Lectura |
| 68 a 99 | Espectro de la vibración, 32 bins | Lectura |
| 132 | Bin dominante de la vibración | Lectura |
| 136 | Corriente de referencia del motor sano | Lectura |
| 138 | RPM calculadas por conteo de delgas | Lectura |
| 140 | Bandera de configuración recibida | Lectura |
| 141 | Comando de actuador y reset | Escritura |
| 142 | Pico de corriente en la ventana de 500 ms | Lectura |
| 143 | Pico de vibración en la ventana de 500 ms | Lectura |
| 148 | Número de delgas | Escritura |
| 149 | Relación de la caja reductora | Escritura |

El byte de alertas tiene definidos los bits de sobrecorriente, vibración alta, sensor desconectado, tiempo de espera del SCADA, falla de FFT y voltaje bajo. En el firmware actual solo el de sobrecorriente se activa desde su rutina de interrupción.

---

## 💻 HMI en Python

La interfaz se llama desde `hilo_HMI.py` y se apoya en PyQt5, pyqtgraph y matplotlib.

### Hilo de comunicación

`hilo_modbus.py` corre en un `QThread` con minimalmodbus a 115200 baudios y consulta al nodo cada 0.4 segundos. Antes de publicar los datos aplica un acondicionamiento que evita que un filtro del dsPIC quede pegado arriba: si la vibración procesada supera 45 y la cruda ya bajó de 25, iguala la procesada a la cruda. Además satura todo en 50 para no deformar las gráficas. Si una lectura falla, emite un paquete de error y la interfaz muestra el aviso de ruido sin congelarse.

### Pestañas

| Pestaña | Qué muestra |
| :--- | :--- |
| Análisis Predictivo | Vibración con EMA e IIR, corriente y espectrograma en cascada. |
| Telemetría Cruda | Valores pico sin filtrar y alertas. |
| Procesamiento de Señal | Subpestañas de EMA contra IIR, True RMS, FFT, RPM y validación de Parseval. |
| Vida Útil | Datos de placa del motor, índice de degradación y proyección de la vida restante. |
| Historial y Tendencia | Firma de arranque, tendencia entre sesiones y tabla de sesiones anteriores. |
| Diagnóstico Avanzado | Envolvente de Hilbert, cepstrum e informe de texto. |
| Respuesta del Filtro IIR | Magnitud, fase y polos y ceros del Butterworth de segundo orden. |

También hay una segunda ventana con los indicadores clave: carga, salud global, RUL, confianza, eficiencia estimada, disponibilidad y diagnóstico.

### Procesamiento

- **Kalman unidimensional** sobre corriente, vibración y RPM.
- **Biquad Butterworth** pasa bajas con coeficientes iguales a los del dsPIC.
- **Envolvente de Hilbert y cepstrum** sobre una ventana larga de 512 muestras, para ver impactos periódicos como los de un rodamiento dañado.
- **Verificación de Parseval** sobre los últimos 64 puntos, como chequeo de que no se perdió ni se alteró información en el enlace.
- **Índice de carga** que combina 85 % de corriente y 15 % de caída de RPM respecto a los datos de placa.
- **Diagnóstico por reglas** que asocia carga, vibración e impactos a causas probables como desbalance, sobrecarga mecánica, fricción o impactos en rodamiento.

### Detección de anomalías

Un Isolation Forest de 50 árboles recibe un vector con vibración, corriente, RPM e impactos, todos normalizados con los datos de placa. Se entrena en segundo plano con muestras tomadas solo en régimen estable y se reentrena cada 200 muestras sanas, para seguir al motor aunque cambie un poco con el tiempo.

### Índice de degradación y vida útil

Durante la calibración, el programa toma 200 muestras en régimen estable y guarda su media y su covarianza. Después calcula el índice de degradación como la distancia de Mahalanobis al estado sano, con una mediana móvil de 7 valores.

```python
delta = x_actual - self._ref_sano
di_raw = float(np.sqrt(max(delta @ self._inv_cov_sano @ delta, 0.0)))
```

Con al menos 30 puntos se ajusta una recta sobre el logaritmo del índice, es decir un modelo exponencial, y se estima en qué momento cruzará el umbral de 6 sigmas. De ahí salen la vida útil restante y un intervalo de confianza. Si la pendiente es nula o negativa, el programa indica que la tendencia es estable.

### Base de datos

`db_manager.py` usa SQLite en modo WAL, y la escritura se hace en un hilo aparte para no frenar la interfaz.

| Tabla | Contenido |
| :--- | :--- |
| `sesiones` | Una fila por sesión de monitoreo, con horas de operación, índice medio y máximo y similitud con la firma de referencia. |
| `muestras` | Serie temporal de corriente, vibración, RPM, valores crudos y bin dominante. |
| `firma_arranque` | Vector de los primeros segundos, pico de corriente y tiempo de estabilización. |
| `eventos` | Sobrecargas y desbalances con causa probable, norma de referencia y acción recomendada. |
| `mantenimiento` | Intervenciones registradas por componente. |

El método `exportar_excel` genera un libro con el resumen de sesiones, la última serie temporal con su gráfico, los eventos y la tendencia del índice.

---

## 📁 Estructura del Repositorio

```
.
├── firmware/
│   ├── main_principal.c     Bucle principal, DSP, interrupciones y máquina de estados
│   ├── modbus_app.c         Capa de aplicación Modbus y mapa de registros
│   ├── modbus_app.h
│   ├── nanomodbus.c         Pila Modbus RTU
│   ├── I2C.c                I2C por software
│   ├── I2C.h
│   ├── LCD_I2C.c            Controlador de la pantalla 4x20
│   ├── LCD_I2C.h
│   ├── config.h             Bits de configuración del dsPIC
│   └── RELOJ.h              FOSC y FCY
└── hmi/
    ├── hilo_HMI.py          Interfaz principal
    ├── hilo_modbus.py       Hilo de comunicación
    ├── db_manager.py        Base de datos y exportación a Excel
    └── firma_arranque.py    Análisis de firmas y tendencias
```

---

## ▶️ Instalación y Uso

### Firmware

1. Abrir el proyecto en MPLAB X con el compilador XC16.
2. Compilar y programar el dsPIC30F4013.
3. Conectar el nodo a la PC por el convertidor USB a RS-485.

### HMI

```bash
pip install numpy scipy scikit-learn matplotlib pyqtgraph PyQt5 minimalmodbus openpyxl
python hilo_HMI.py
```

1. Escribir el puerto serie. En Linux el valor por defecto es `/dev/ttyUSB0`.
2. Indicar las RPM objetivo, el número de delgas y la relación de reducción.
3. Pulsar Iniciar. El nodo recibe la configuración, espera 3 segundos de banda muerta y calibra hasta los 10 segundos.
4. En la pestaña de vida útil, elegir un perfil de motor o cargar los datos de placa, escribir el tag del equipo y confirmar.

---

## 📊 Resultados

Las pruebas las hice en un banco de laboratorio con un motor de corriente continua pequeño.

| Prueba | Requisito | Resultado |
| :--- | :--- | :--- |
| Corte por sobrecorriente | Menos de 1 ms | Del orden de microsegundos, medido con osciloscopio. |
| Error del True RMS contra multímetro | Menos de 5 % | Alrededor de 1 %. |
| Desviación de Parseval | Menos de 1 % | Menos de 0.1 %. |
| Clasificación con Isolation Forest | Más de 95 % | 98.5 %. |
| Ajuste de la regresión para el RUL | R² mayor a 0.9 | 0.94. |
| Alerta celular | Menos de 5 s | Entre 2 y 3 s. |
| Pico espectral con desbalance a 1800 RPM | Bin 4 | Bin 4, que corresponde a 30 Hz. |

---

## ⚠️ Limitaciones

- Todo se probó en laboratorio y con un motor pequeño. No lo he puesto en una línea de producción real.
- El Isolation Forest y el cálculo del RUL corren en la PC. Una mejora natural sería llevar el clasificador al dsPIC.
- El modelo de degradación supone una tendencia exponencial y necesita una calibración limpia del motor sano para dar buenos resultados.
- En el firmware actual solo está conectada la alerta de sobrecorriente. Los demás bits del byte de alertas quedaron reservados.

---

## 👤 Autor

**Favio Alexander Campolo Ruiz**
Ingeniería Electrónica, Universidad Tecnológica del Perú.

