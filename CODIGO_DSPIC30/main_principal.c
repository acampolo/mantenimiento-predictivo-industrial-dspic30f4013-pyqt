// main_principal.c
// Nodo IoT - Mantenimiento Predictivo Industrial para dsPIC30F4013
// Autor: Favio Alexander

#include <xc.h>
#include <stdint.h>
#include <string.h>
#include <stdbool.h>
#include <dsp.h>
#include "config.h"
#include "RELOJ.h"
#include <libpic30.h>
#include "modbus_app.h"
#include "I2C.h"
#include "LCD_I2C.h"

#define FFT_SAMPLES   64

// HAL - Asignación de pines
#define PIN_ACTUADOR         LATCbits.LATC13
#define TRIS_ACTUADOR        TRISCbits.TRISC13
#define LED_ESTADO_ALIVE     LATBbits.LATB3
#define TRIS_LED_ESTADO      TRISBbits.TRISB3
#define LED_ADC_DEBUG        LATBbits.LATB9
#define TRIS_LED_ADC         TRISBbits.TRISB9
#define LED_LTE_TX           LATBbits.LATB10
#define TRIS_LED_LTE         TRISBbits.TRISB10
#define LED_ALARMA           LATBbits.LATB8
#define TRIS_LED_ALARMA      TRISBbits.TRISB8
#define PIN_BOTON_TEST       PORTDbits.RD9
#define TRIS_BOTON_TEST      TRISDbits.TRISD9

// I2C por software mediante parche para liberar los pines por hardware
#define SCL_SOFT_OUT         LATBbits.LATB12
#define SCL_SOFT_DIR         TRISBbits.TRISB12
#define SDA_SOFT_OUT         LATBbits.LATB11
#define SDA_SOFT_IN          PORTBbits.RB11
#define SDA_SOFT_DIR         TRISBbits.TRISB11

// --- Espacio DSP y Tablas ---

// Alineación obligatoria a 128 bytes en la memoria Y debido a que el módulo DSP genera Address Error en caso contrario
fractcomplex workspace_fft[FFT_SAMPLES] 
    __attribute__((space(ymemory), aligned(FFT_SAMPLES * 2 * 2)));

const fractcomplex twiddleFactors[32] 
    __attribute__((space(auto_psv), aligned(128))) =
{
    {0x7FFF,0x0000}, {0x7F62,0xF374}, {0x7D8A,0xE707}, {0x7A7D,0xDAD8},
    {0x7642,0xCF14}, {0x70E3,0xC3A9}, {0x6A6E,0xB8E3}, {0x62FB,0xAECD},
    {0x5A82,0xA57E}, {0x5134,0x9D06}, {0x471D,0x9592}, {0x3C57,0x8F1E},
    {0x30FC,0x89BE}, {0x2528,0x8583}, {0x18FA,0x8276}, {0x0C8C,0x809E},
    {0x0000,0x8000}, {0xF374,0x809E}, {0xE707,0x8276}, {0xDAD8,0x8583},
    {0xCF14,0x89BE}, {0xC3A9,0x8F1E}, {0xB8E3,0x9592}, {0xAECD,0x9D06},
    {0xA57E,0xA57E}, {0x9D06,0xAECD}, {0x9592,0xB8E3}, {0x8F1E,0xC3A9},
    {0x89BE,0xCF14}, {0x8583,0xDAD8}, {0x8276,0xE707}, {0x809E,0xF374}
};

const fractional windowHanning[64] __attribute__((space(auto_psv))) =
{
       0,   134,   536,  1205,  2133,  3307,  4712,  6330,
    8138, 10116, 12241, 14489, 16835, 19251, 21712, 24186,
   26636, 29026, 31323, 32767, 32767, 31323, 29026, 26636,
   24186, 21712, 19251, 16835, 14489, 12241, 10116,  8138,
    6330,  4712,  3307,  2133,  1205,   536,   134,     0,
       0,   134,   536,  1205,  2133,  3307,  4712,  6330,
    8138, 10116, 12241, 14489, 16835, 19251, 21712, 24186,
   26636, 29026, 31323, 32767, 32767, 31323, 29026, 26636
};

// --- Máquina de Estados ---
typedef enum
{
    FASE_ESPERA_RPM   = 0,
    FASE_BANDA_MUERTA = 1,
    FASE_CALIBRACION  = 2,
    FASE_MONITOREO    = 3
} fase_operacion_t;

typedef struct
{
    volatile uint16_t         buffer_index;
    volatile uint16_t         bandera_buffer_lleno; 
    uint16_t                  buffer_corriente[FFT_SAMPLES];
    uint16_t                  buffer_vibracion[FFT_SAMPLES];
    volatile uint16_t         corriente_final;
    volatile uint16_t         vibracion_final;
    volatile fase_operacion_t fase;
    uint16_t                  frecuencia_muestreo_hz;

    union
    {
        uint8_t byte_alertas;
        struct
        {
            unsigned sobrecorriente : 1;
            unsigned vibracion_alta : 1;
            unsigned sensor_desc    : 1;
            unsigned timeout_scada  : 1;
            unsigned falla_fft      : 1;
            unsigned voltaje_bajo   : 1;
        } bits;
    } alertas;
} estado_sistema_t;

estado_sistema_t estado_actual;

// --- RAM Persistente ---

// Variables configuradas para sobrevivir a un reset por Math Error o caída de voltaje
uint16_t config_rpm_persistente    __attribute__((persistent));
uint16_t config_delgas_persistente __attribute__((persistent));
uint16_t config_reduct_persistente __attribute__((persistent));
uint16_t flag_memoria_valida       __attribute__((persistent));

uint32_t picos_escobillas    = 0;
int32_t  filtered_hp_int     = 0;
int32_t  last_input_int      = 0;

int32_t  ema_corriente    = 0;
int32_t  ema_vibracion_ac = 0;

volatile uint16_t pico_vib_500ms      = 0;
volatile uint16_t pico_corr_adc_500ms = 0;

uint32_t tiempo_inicio_motor   = 0;
uint32_t suma_calibracion_corr = 0;
uint16_t muestras_calibracion  = 0;
uint16_t corriente_referencia_sana = 0;

// Constante calibrada para el ACS758. Se debe cambiar si se utiliza otro modelo distinto al de 50A.
const uint32_t MULT_ACS758 = 2774;

lcd_t miPantalla;

// Prototipos
void     config_timer3_muestreo(void);
void     config_adc_sincrono(void);
void     config_pwm_motor(void);
void     config_interlock_emergencia(void);
void     actualizar_pantalla_lcd(lcd_t *lcd, uint16_t rpm, uint16_t corr, uint16_t vib, uint8_t alertas, uint8_t fase);
void     Ajustar_Muestreo_Dinamico(uint16_t rpm);
void     procesar_fft_canal(uint16_t* fuente, uint16_t* destino_modbus);
uint16_t obtener_pico_espectral(uint16_t* fft_result);
uint32_t integer_sqrt(uint32_t n);
uint16_t entero_a_string(uint16_t num, char* str);

extern uint32_t get_current_time_ms(void);

// --- MAIN ---
int main(void)
{
    // Limpieza de puertos para evitar estados fantasma durante el arranque
    PIN_ACTUADOR = 0;      
    TRIS_ACTUADOR = 0;
    
    LED_ESTADO_ALIVE = 0;  
    TRIS_LED_ESTADO = 0;
    
    LED_ADC_DEBUG = 0;     
    TRIS_LED_ADC = 0;
    
    LED_LTE_TX = 0;        
    TRIS_LED_LTE = 0;
    
    LED_ALARMA = 0;        
    TRIS_LED_ALARMA = 0;
    
    SCL_SOFT_OUT = 0;      
    SCL_SOFT_DIR = 1;
    
    SDA_SOFT_OUT = 0;      
    SDA_SOFT_DIR = 1;
    
    TRIS_BOTON_TEST = 1;

    memset(&estado_actual, 0, sizeof(estado_sistema_t));

    config_timer3_muestreo();
    config_adc_sincrono();
    config_pwm_motor();
    config_interlock_emergencia();
    Modbus_Init();

    miPantalla.lcd_address   = LCD_I2C_ADDRESS_0;
    miPantalla.lcd_4bit      = LCD_4BITS;
    miPantalla.lcd_font      = LCD_4BIT_2LINEAS;
    miPantalla.lcd_display   = LCD_DISPLAY_ON;
    miPantalla.lcd_inc_ddram = LCD_ENTRY_INCREMENTO_;
    miPantalla.lcd_clear     = LCD_BORRARLCD;

    I2C_Init();
    LCD_I2C_Init(&miPantalla);
    LCD_I2C_XY(&miPantalla, 0, 0);

    // Revisión de la causa de reinicio mediante lectura del registro RCON
    uint16_t rcon_reg = RCON;
    RCON = 0; 

    // Se fuerza una configuración nueva en caso de pulsación del MCLR físico o caída abrupta de voltaje
    if ((rcon_reg & 0x0080) || (rcon_reg & 0x0003)) 
    { 
        flag_memoria_valida = 0; 
    }

    // Recuperación silenciosa para inicializar el sistema tras un error de software
    if (flag_memoria_valida == 0xA5A5)
    {
        server_registers[0]   = config_rpm_persistente;
        server_registers[148] = config_delgas_persistente;
        server_registers[149] = config_reduct_persistente;
        server_registers[140] = 3; 
        
        Ajustar_Muestreo_Dinamico(config_rpm_persistente);
        
        OC1RS = 0; // Ciclo de trabajo máximo en PWM considerando lógica inversa
        estado_actual.fase  = FASE_BANDA_MUERTA;
        tiempo_inicio_motor = get_current_time_ms();
    }
    else
    {
        estado_actual.fase = FASE_ESPERA_RPM;
        
        // Retardo visual aplicado únicamente durante un arranque en frío
        int i;
        for (i = 0; i < 20; i++) 
        { 
            __delay_ms(100); 
            ClrWdt(); // Se reinicia el watchdog timer ya que el retardo es prolongado
            Modbus_Tasks();
        }
    }

    actualizar_pantalla_lcd(&miPantalla, 0, 0, 0, 0, estado_actual.fase);

    uint32_t tiempo_ultima_telemetria = 0;
    
    INTCON1bits.NSTDIS = 1;
    // Activación de saturación en los acumuladores DSP con el fin de prevenir desbordamientos en la FFT
    CORCONbits.SATA  = 1;
    CORCONbits.SATB  = 1;
    CORCONbits.SATDW = 1;

    for (;;)
    {
        ClrWdt();
        Modbus_Tasks();

        // --- Lógica de enclavamiento y emergencia ---
        if ((estado_actual.alertas.byte_alertas > 0) || (server_registers[141] == 1))
        {
            OC1RS = 2949;      // Parada de emergencia activada
            PIN_ACTUADOR = 1;  
            LED_ALARMA = 1;    
            
            // Retorno a la condición de inicio siempre que se envíe un reset desde el SCADA y no existan fallas activas
            if (server_registers[141] == 1 && estado_actual.alertas.byte_alertas == 0)
            {
                estado_actual.fase = FASE_ESPERA_RPM;
            }
        }
        else
        {
            LED_ALARMA = 0;
            PIN_ACTUADOR = 0;  
            
            if (estado_actual.fase != FASE_ESPERA_RPM)
            {
                OC1RS = 0;    
            }
            else
            {
                OC1RS = 2949; 
            }
        }

        // --- Telemetría a la LCD ---
        // Actualización de telemetría en la LCD cada 500ms con el objetivo de evitar parpadeos visuales
        if (get_current_time_ms() - tiempo_ultima_telemetria >= 500)
        {
            LED_ESTADO_ALIVE    = !LED_ESTADO_ALIVE;
            
            pico_vib_500ms      = 0;
            pico_corr_adc_500ms = 0;
            
            actualizar_pantalla_lcd(
                &miPantalla,
                server_registers[0],
                estado_actual.corriente_final,
                estado_actual.vibracion_final,
                estado_actual.alertas.byte_alertas,
                estado_actual.fase
            );
            
            tiempo_ultima_telemetria = get_current_time_ms();
        }

        // --- Máquina de estados principal ---
        if (estado_actual.fase == FASE_ESPERA_RPM)
        {
            uint16_t rpm = server_registers[0];
            
            if (rpm >= 10 && rpm <= 10000)
            {
                config_rpm_persistente    = rpm;
                config_delgas_persistente = server_registers[148];
                config_reduct_persistente = server_registers[149];
                server_registers[140]     = 3; 
                
                flag_memoria_valida = 0xA5A5; // Número de validación para confirmar el guardado correcto en memoria
                
                Ajustar_Muestreo_Dinamico(rpm);
                
                estado_actual.fase  = FASE_BANDA_MUERTA;
                tiempo_inicio_motor = get_current_time_ms();
            }
        }
        else if (estado_actual.bandera_buffer_lleno == 1)
        {
            T3CONbits.TON = 0; // Apagado del timer principal con el propósito de evitar sobreescritura del buffer durante el procesamiento

            // Guardado de variables clave continuamente por si ocurre un microcorte
            config_rpm_persistente    = server_registers[0];
            config_delgas_persistente = server_registers[148];
            config_reduct_persistente = server_registers[149];

            IEC0bits.ADIE = 0; // Interbloqueo rápido para extraer una copia limpia de los picos
            uint32_t picos_snapshot = picos_escobillas;
            picos_escobillas = 0;
            IEC0bits.ADIE = 1;

            uint16_t num_delgas = server_registers[148];
            if (num_delgas == 0)
            {
                num_delgas = 3; // Asignación por defecto ya que evita una división por cero si la configuración Modbus es incorrecta
            }

            // Cálculo de RPM real según frecuencia de muestreo y número de delgas
            uint32_t rpm_calculado = (picos_snapshot * (uint32_t)estado_actual.frecuencia_muestreo_hz * 60UL) / (FFT_SAMPLES * num_delgas);
            server_registers[138] = (uint16_t)rpm_calculado;

            uint32_t suma_cuadrados = 0;
            
            int j;
            for (j = 0; j < FFT_SAMPLES; j++)
            {
                // Eliminación del offset del ADC mediante resta del punto medio de rango
                int32_t valor_ac = (int32_t)estado_actual.buffer_corriente[j] - 2048;
                suma_cuadrados += (uint32_t)(valor_ac * valor_ac);
            }

            // Cálculo RMS real. Se divide entre 64 muestras utilizando desplazamiento de bits a la derecha
            uint32_t media_cuadratica = suma_cuadrados >> 6;
            uint16_t true_rms_crudo   = (uint16_t)integer_sqrt(media_cuadratica);
            
            uint32_t true_rms_ma      = ((uint32_t)true_rms_crudo * MULT_ACS758) / 1000UL;
            
            if (true_rms_ma < 65) 
            {
                true_rms_ma = 0; // Filtro aplicado para eliminar el ruido de fondo del sensor ACS758
            }

            // Filtro exponencial aplicado para suavizar las lecturas mostradas en la pantalla
            ema_corriente += ((int32_t)true_rms_ma - ema_corriente) >> 3;
            estado_actual.corriente_final = (uint16_t)ema_corriente;

            uint32_t tiempo_transcurrido = get_current_time_ms() - tiempo_inicio_motor;

            // Manejo de la rampa de arranque del motor donde se ignoran los picos falsos
            if (estado_actual.fase == FASE_BANDA_MUERTA)
            {
                if (tiempo_transcurrido >= 3000)
                {
                    estado_actual.fase    = FASE_CALIBRACION;
                    suma_calibracion_corr = 0;
                    muestras_calibracion  = 0;
                }
            }
            else if (estado_actual.fase == FASE_CALIBRACION)
            {
                suma_calibracion_corr += estado_actual.corriente_final;
                muestras_calibracion++;
                
                if (tiempo_transcurrido >= 10000)
                {
                    estado_actual.fase = FASE_MONITOREO;
                    
                    if (muestras_calibracion > 0)
                    {
                        corriente_referencia_sana = suma_calibracion_corr / muestras_calibracion;
                        server_registers[136]     = corriente_referencia_sana;
                    }
                }
            }
            else if (estado_actual.fase == FASE_MONITOREO)
            {
                procesar_fft_canal(estado_actual.buffer_corriente, &server_registers[4]);
                procesar_fft_canal(estado_actual.buffer_vibracion, &server_registers[68]);
                
                server_registers[132] = obtener_pico_espectral(&server_registers[68]);
            }

            // Actualización del mapa Modbus final
            server_registers[1] = estado_actual.corriente_final;
            server_registers[2] = estado_actual.vibracion_final;
            server_registers[3] = estado_actual.alertas.byte_alertas;

            estado_actual.buffer_index         = 0;
            estado_actual.bandera_buffer_lleno = 0;

            T3CONbits.TON = 1; // Reactivación del muestreo
        }
    }

    return 0;
}

// --- Algoritmos DSP ---

uint16_t obtener_pico_espectral(uint16_t* fft_result)
{
    uint16_t max_val = 0;
    uint16_t max_index = 0; 
    
    // Omisión de la componente continua en el bin 0 y el ruido de baja frecuencia en el bin 1
    int i;
    for (i = 2; i < (FFT_SAMPLES / 2); i++)
    {
        if (fft_result[i] > max_val) 
        { 
            max_val = fft_result[i]; 
            max_index = i; 
        }
    }
    
    if (max_val < 5) return 0; // Retorno cero debido a exceso de ruido y ausencia de un pico claro
    
    return max_index;
}

void procesar_fft_canal(uint16_t* fuente, uint16_t* destino_modbus)
{
    int i;
    int32_t signal_val;
    int32_t suma_dc = 0;
    
    // Paso 1. Cálculo del offset DC mediante suma y división entre 64 muestras
    for (i = 0; i < FFT_SAMPLES; i++) 
    {
        suma_dc += fuente[i];
    }
    int32_t offset_dc = suma_dc >> 6; 
    
    // Paso 2. Aplicación de ventana de Hanning y amplificación
    for (i = 0; i < FFT_SAMPLES; i++)
    {
        signal_val = (int32_t)fuente[i] - offset_dc;
        signal_val = (signal_val * (int32_t)windowHanning[i]) >> 15;
        
        // Amplificación por 16 en la amplitud para maximizar el uso del formato Q15. Una señal muy ruidosa causará saturación.
        signal_val = signal_val << 4;
        
        // Límite estricto de saturación por seguridad algorítmica
        if (signal_val > 32767) signal_val = 32767;
        if (signal_val < -32768) signal_val = -32768;
        
        workspace_fft[i].real = (int16_t)signal_val;
        workspace_fft[i].imag = 0;
    }
    
    // Paso 3. Ejecución de la FFT in-place mediante el acelerador por hardware del dsPIC
    FFTComplexIP(6, workspace_fft, (fractcomplex*)twiddleFactors, (int)__builtin_psvpage(&twiddleFactors));
    BitReverseComplex(6, workspace_fft);
    SquareMagnitudeCplx(32, workspace_fft, (fractional*)destino_modbus);
}

// Algoritmo de raíz cuadrada mediante corrimientos lógicos. Su uso evita la importación de librerías matemáticas pesadas.
// Algoritmo de aproximación sucesiva. Mantener estructura exacta.
uint32_t integer_sqrt(uint32_t n)
{
    uint32_t root = 0;
    uint32_t bit  = 1UL << 30;
    
    while (bit > n)  
    {
        bit >>= 2;
    }
    
    while (bit != 0)
    {
        if (n >= root + bit) 
        { 
            n -= root + bit; 
            root = (root >> 1) + bit; 
        }
        else 
        {
            root >>= 1;
        }
        
        bit >>= 2;
    }
    
    return root;
}

// --- Trampas de Error e Interrupciones ---

void __attribute__((__interrupt__, no_auto_psv)) _INT0Interrupt(void)
{
    IFS0bits.INT0IF = 0;
    
    OC1RS = 2949; 
    
    estado_actual.alertas.bits.sobrecorriente = 1;
    PIN_ACTUADOR = 1; 
}

void __attribute__((__interrupt__, no_auto_psv)) _ADCInterrupt(void)
{
    IFS0bits.ADIF = 0;
    
    LED_ADC_DEBUG = !LED_ADC_DEBUG;

    // Variables estáticas utilizadas para conservar un formato de memoria estable
    static uint16_t raw_i_x1;
    static uint16_t raw_i_x11;
    static uint16_t raw_v_x1;
    static uint16_t raw_v_x11;

    static uint16_t raw_i_final;
    static uint16_t raw_v_final;

    static int32_t ac_reconstruida;
    static int32_t final_val;

    static int32_t delta;
    static int32_t sum_hp;

    static int32_t corr_ac;
    static int16_t vib_ac;
    static uint32_t pico_ma;

    static bool flag_pico_alto = false;

    raw_i_x1  = ADCBUF0;
    raw_i_x11 = ADCBUF1;
    raw_v_x1  = ADCBUF2;
    raw_v_x11 = ADCBUF3;

    if (raw_i_x11 > 4090 || raw_i_x11 < 5)
    {
        ac_reconstruida = ((int32_t)raw_i_x1 - 2048) * 11;
        final_val       = ac_reconstruida + 2048;
        
        if (final_val < 0)    
        {
            final_val = 0;
        }
        else if (final_val > 4095) 
        {
            final_val = 4095;   
        }
        
        raw_i_final = (uint16_t)final_val;
    }
    else 
    { 
        raw_i_final = raw_i_x11; 
    }

    if (raw_v_x11 > 4090 || raw_v_x11 < 5)
    {
        ac_reconstruida = ((int32_t)raw_v_x1 - 2048) * 11;
        final_val       = ac_reconstruida + 2048;
        
        if (final_val < 0)    
        {
            final_val = 0;
        }
        else if (final_val > 4095) 
        {
            final_val = 4095;
        }
        
        raw_v_final = (uint16_t)final_val;
    }
    else 
    { 
        raw_v_final = raw_v_x11; 
    }

    delta   = (int32_t)raw_i_final - last_input_int;
    last_input_int  = (int32_t)raw_i_final;   
    
    sum_hp = filtered_hp_int + delta;
    
    if (sum_hp > 50000L) 
    {
        sum_hp = 50000L;
    }
    else if (sum_hp < -50000L) 
    {
        sum_hp = -50000L;
    }
    
    filtered_hp_int = (31130L * sum_hp) >> 15;

    // Conteo por flanco enjaulado en la ventana de tiempo
    if (estado_actual.bandera_buffer_lleno == 0)
    {
        if (filtered_hp_int > 5)
        {
            if (!flag_pico_alto) 
            {
                if (picos_escobillas < 0xFFFFFFFFUL) 
                {
                    picos_escobillas++;
                }
                flag_pico_alto = true;
            }
        }
        else if (filtered_hp_int < -5)
        {
            flag_pico_alto = false;
        }

        estado_actual.buffer_corriente[estado_actual.buffer_index] = raw_i_final;
        estado_actual.buffer_vibracion[estado_actual.buffer_index] = raw_v_final;
        estado_actual.buffer_index++;
        
        if (estado_actual.buffer_index >= FFT_SAMPLES)
        {
            estado_actual.bandera_buffer_lleno = 1;
        }
    }

    corr_ac = (int32_t)raw_i_final - 2048;
    
    if (corr_ac < 0) 
    {
        corr_ac = -corr_ac;
    }

    if ((uint16_t)corr_ac > pico_corr_adc_500ms)
    {
        pico_corr_adc_500ms = (uint16_t)corr_ac;
        
        pico_ma = ((uint32_t)pico_corr_adc_500ms * MULT_ACS758) / 1000UL;
        
        if (pico_ma < 65) 
        {
            pico_ma = 0;
        }
        
        server_registers[142] = (uint16_t)pico_ma;
    }

    vib_ac = (int16_t)raw_v_final - 2048;
    
    if (vib_ac < 0) 
    {
        vib_ac = -vib_ac;
    }

    if ((uint16_t)vib_ac > pico_vib_500ms)
    {
        pico_vib_500ms        = (uint16_t)vib_ac;
        server_registers[143] = pico_vib_500ms;
    }

    ema_vibracion_ac += ((int32_t)vib_ac - ema_vibracion_ac) >> 3;
    estado_actual.vibracion_final = (uint16_t)ema_vibracion_ac;
    
    if (estado_actual.vibracion_final < 10) 
    {
        estado_actual.vibracion_final = 0;
    }
}

void __attribute__((interrupt, no_auto_psv)) _AddressError(void)
{
    INTCON1bits.ADDRERR = 0;
    __asm__ volatile ("reset"); // Reinicio completo del sistema por acceso indebido a memoria
}

void __attribute__((interrupt, no_auto_psv)) _StackError(void)
{
    INTCON1bits.STKERR = 0;
    __asm__ volatile ("reset");
}

void __attribute__((interrupt, no_auto_psv)) _MathError(void)
{
    INTCON1bits.MATHERR = 0;
    // Limpieza de acumuladores DSP debido a un error matemático como división por cero
    __asm__ volatile ("clr A");
    __asm__ volatile ("clr B");
}

// --- Funciones LCD y Configuración ---

uint16_t entero_a_string(uint16_t num, char* str)
{
    if (num == 0) { str[0] = '0'; str[1] = '\0'; return 1; }
    uint16_t i = 0; 
    char temp[12];
    
    while (num > 0) { temp[i++] = (char)((num % 10) + '0'); num /= 10; }
    uint16_t j = 0;
    while (i > 0) { str[j++] = temp[--i]; }
    str[j] = '\0';
    return j;
}

void actualizar_pantalla_lcd(lcd_t *lcd, uint16_t rpm, uint16_t corr, uint16_t vib, uint8_t alertas, uint8_t fase)
{
    char buf[21]; 
    uint16_t idx;

    if (fase == FASE_ESPERA_RPM)
    {
        LCD_I2C_XY(lcd, 0, 0); 
        LCD_I2C_Cadena(lcd, "SISTEMA EN ESPERA   ");
        
        LCD_I2C_XY(lcd, 1, 0); 
        LCD_I2C_Cadena(lcd, "-> Inserte RPM      ");
        
        LCD_I2C_XY(lcd, 2, 0); 
        LCD_I2C_Cadena(lcd, "                    ");
        
        LCD_I2C_XY(lcd, 3, 0); 
        LCD_I2C_Cadena(lcd, "                    ");
        
        return;
    }

    if (fase == FASE_BANDA_MUERTA)
    {
        LCD_I2C_XY(lcd, 0, 0);
        LCD_I2C_Cadena(lcd, "ARRANCANDO MOTOR... ");
    }
    else if (fase == FASE_CALIBRACION)
    {
        LCD_I2C_XY(lcd, 0, 0);
        LCD_I2C_Cadena(lcd, "CALIBRANDO REF...   ");
    }
    else
    {
        LCD_I2C_XY(lcd, 0, 0);
        
        if (alertas == 0)
        {
            LCD_I2C_Cadena(lcd, "ESTADO: MAQUINA OK  ");
        }
        else
        {
            LCD_I2C_Cadena(lcd, "!!! ALERTA ACTIVA !!!");
        }
    }

    strcpy(buf, "RPM: "); 
    idx = 5;
    idx += entero_a_string(rpm, &buf[idx]);
    
    while (idx < 20) { buf[idx++] = ' '; }
    buf[20] = '\0'; 
    LCD_I2C_XY(lcd, 1, 0); 
    LCD_I2C_Cadena(lcd, buf);

    strcpy(buf, "CORR: "); 
    idx = 6;
    idx += entero_a_string(corr, &buf[idx]);
    strcpy(&buf[idx], " mA"); 
    idx += 3;
    
    while (idx < 20) { buf[idx++] = ' '; }
    buf[20] = '\0'; 
    LCD_I2C_XY(lcd, 2, 0); 
    LCD_I2C_Cadena(lcd, buf);

    strcpy(buf, "VIB:  "); 
    idx = 6;
    idx += entero_a_string(vib, &buf[idx]);
    strcpy(&buf[idx], " raw"); 
    idx += 4;
    
    while (idx < 20) { buf[idx++] = ' '; }
    buf[20] = '\0'; 
    LCD_I2C_XY(lcd, 3, 0); 
    LCD_I2C_Cadena(lcd, buf);
}

void Ajustar_Muestreo_Dinamico(uint16_t rpm)
{
    if (rpm < 10 || rpm > 10000) 
    {
        return;
    }

    uint32_t rpm_rotor_interno = rpm;
    uint16_t reductora = server_registers[149];
    
    if (reductora > 0)
    {
        rpm_rotor_interno = (uint32_t)rpm * reductora;
    }

    uint32_t f_muestreo = (rpm_rotor_interno * 16UL) / 60UL;

    if (f_muestreo > 5000) f_muestreo = 5000;
    if (f_muestreo < 250) f_muestreo = 250;
    if (f_muestreo == 0) f_muestreo = 250; 

    estado_actual.frecuencia_muestreo_hz = (uint16_t)f_muestreo;
    
    T3CONbits.TON = 0;
    TMR3 = 0;

    unsigned long temp_pr3 = (29491200UL / f_muestreo) - 1UL;

    if (temp_pr3 <= 65535UL) 
    { 
        T3CONbits.TCKPS = 0b00; 
        PR3 = (uint16_t) temp_pr3;         
    }
    else if ((temp_pr3 / 8UL) <= 65535UL) 
    { 
        T3CONbits.TCKPS = 0b01; 
        PR3 = (uint16_t)(temp_pr3 / 8UL);
    }
    else if ((temp_pr3 / 64UL) <= 65535UL) 
    { 
        T3CONbits.TCKPS = 0b10; 
        PR3 = (uint16_t)(temp_pr3 / 64UL);
    }
    else                                         
    { 
        T3CONbits.TCKPS = 0b11; 
        PR3 = (uint16_t)(temp_pr3 / 256UL);
    }

    T3CONbits.TON = 1;
}

void config_timer3_muestreo(void)
{
    T3CON = 0; 
    IFS0bits.T3IF = 0; 
    IEC0bits.T3IE = 0; 
    PR3 = 14745;
}

void config_adc_sincrono(void)
{
    ADCON1bits.ADON  = 0; 
    ADPCFG = 0xFFCC; 
    ADCON2bits.VCFG = 0; 
    ADCON1bits.FORM = 0;
    
    ADCON2bits.CSCNA = 1; 
    ADCSSL = 0x0033; 
    ADCON2bits.SMPI = 3;
    
    ADCON1bits.SSRC  = 0b010; 
    ADCON3bits.SAMC = 15; 
    ADCON3bits.ADCS = 20;
    
    ADCON1bits.ASAM  = 1; 
    IFS0bits.ADIF = 0; 
    IEC0bits.ADIE = 1;
    
    IPC2bits.ADIP    = 5; 
    ADCON1bits.ADON = 1;
}

void config_interlock_emergencia(void)
{
    INTCON2bits.INT0EP = 0; 
    IFS0bits.INT0IF = 0; 
    IPC0bits.INT0IP = 7; 
    IEC0bits.INT0IE = 1;
}

void config_pwm_motor(void)
{
    LATDbits.LATD0 = 1; 
    TRISDbits.TRISD0 = 0;
    
    T2CON = 0; 
    T2CONbits.TCKPS = 0; 
    PR2 = 2949;
    
    OC1R = 2949; 
    OC1RS = 2949; 
    OC1CONbits.OCM = 0b110; 
    
    T2CONbits.TON = 1;
}