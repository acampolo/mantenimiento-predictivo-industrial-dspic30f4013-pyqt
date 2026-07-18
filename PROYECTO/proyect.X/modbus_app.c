/* ===============================================================================================
 * ARCHIVO:          modbus_app.c
 * AUTOR:            Favio Alexander
 * PROYECTO:         Nodo IoT de Monitoreo Predictivo Industrial - Edge Computing
 * HARDWARE:         dsPIC30F4013 - UART2, Timer1, FCY=29.4912 MHz
 * DESCRIPCIÓN:      Capa de comunicación Modbus RTU Esclavo utilizando NanoModbus
 * GESTIÓN VISUAL:   Formato de Programación Vertical Estricto
 * =============================================================================================== */

#include <xc.h>
#include <stdint.h>
#include <stdbool.h>
#include "RELOJ.h"
#include <libpic30.h>
#include "modbus_app.h" 
#include "nanomodbus.h"

/* -----------------------------------------------------------------------------------------------
 * DEFINICIONES DE HARDWARE Y PROTOCOLO
 * ----------------------------------------------------------------------------------------------- */
#define RS485_CTRL          LATDbits.LATD1  
#define RX_BUFFER_SIZE      64 
#define RTU_SERVER_ADDRESS  1       

/* Búfer circular para la recepción asíncrona de la UART2 */
volatile uint8_t rx_buffer[RX_BUFFER_SIZE];
volatile uint16_t rx_head = 0;
volatile uint16_t rx_tail = 0;

nmbs_platform_conf platform_conf;
nmbs_callbacks callbacks;
nmbs_t nmbs;

/* Registros del servidor Modbus compartidos externamente con el main */
uint16_t server_registers[REGS_ADDR_MAX + 1] = {0}; 
volatile uint32_t system_ms = 0;

/* ===============================================================================================
 * CONFIGURACIÓN DE PERIFÉRICOS DE COMUNICACIÓN
 * =============================================================================================== */

void config_Timer1_1ms(void) 
{
    T1CON = 0;              
    T1CONbits.TCKPS = 0;    
    TMR1 = 0;               
    PR1 = 29490;            
    IFS0bits.T1IF = 0;      
    IEC0bits.T1IE = 1;      
    IPC0bits.T1IP = 2;      
    T1CONbits.TON = 1;      
}

void config_UART2(void) 
{
    TRISDbits.TRISD1 = 0;   
    RS485_CTRL = 0;         
    U2MODEbits.UARTEN = 0;  
    U2BRG = 15;             
    U2MODEbits.PDSEL = 0b00; 
    U2MODEbits.STSEL = 0;    
    IFS1bits.U2RXIF = 0;    
    IEC1bits.U2RXIE = 1;    
    IPC6bits.U2RXIP = 4;    
    U2MODEbits.UARTEN = 1;  
    U2STAbits.UTXEN = 1;    
}

/* ===============================================================================================
 * RUTINAS DE INTERRUPCIÓN (ISR)
 * =============================================================================================== */

void __attribute__((__interrupt__, no_auto_psv)) _T1Interrupt(void) 
{
    IFS0bits.T1IF = 0;
    system_ms++; 
}

void __attribute__((__interrupt__, no_auto_psv)) _U2RXInterrupt(void) 
{
    IFS1bits.U2RXIF = 0; 
    
    /* Extraer tramas completas del búfer FIFO físico de hardware */
    while (U2STAbits.URXDA) 
    {
        rx_buffer[rx_head] = U2RXREG;
        rx_head = (rx_head + 1) % RX_BUFFER_SIZE;
    }
    
    /* Blindaje contra desbordamiento por ruido electromagnético severo */
    if (U2STAbits.OERR) 
    {
        U2STAbits.OERR = 0; 
    }
}

/* ===============================================================================================
 * FUNCIONES DE TIEMPO Y CONTROL SERIAL
 * =============================================================================================== */

uint32_t get_current_time_ms(void) 
{
    uint32_t t1;
    uint32_t t2;
    
    do 
    { 
        t1 = system_ms; 
        t2 = system_ms; 
    } 
    while (t1 != t2);
    
    return t1;
}

int32_t write_serial(const uint8_t* buf, uint16_t count, int32_t byte_timeout_ms, void* arg) 
{
    RS485_CTRL = 1;         
    __delay_us(50);         
    
    uint16_t i;
    for (i = 0; i < count; i++) 
    {
        while (U2STAbits.UTXBF == 1) 
        { 
            ClrWdt(); 
        }
        
        U2TXREG = buf[i];   
    }
    
    while (U2STAbits.TRMT == 0) 
    { 
        ClrWdt(); 
    }
    
    __delay_ms(1);
    RS485_CTRL = 0;         
    
    return count; 
}

int32_t read_serial(uint8_t* buf, uint16_t count, int32_t byte_timeout_ms, void* arg) 
{
    int32_t bytes_read = 0;
    
    while (bytes_read < count) 
    {
        uint32_t start_time = get_current_time_ms();
        bool byte_received = false;
        
        while (1) 
        {
            ClrWdt(); 
            
            if (rx_tail != rx_head) 
            { 
                buf[bytes_read++] = rx_buffer[rx_tail];
                rx_tail = (rx_tail + 1) % RX_BUFFER_SIZE;
                byte_received = true; 
                
                break; 
            }
            
            if (byte_timeout_ms >= 0 && ((get_current_time_ms() - start_time) >= (uint32_t)byte_timeout_ms)) 
            {
                break; 
            }
        }
        
        if (!byte_received) 
        {
            break;
        }
    }
    
    return bytes_read; 
}

void onError(void) 
{
    /* Manejador de excepciones de transporte Modbus */
}

/* ===============================================================================================
 * INTERFACES DE CALLBACKS DE NANOMODBUS (FIX ALINEACIÓN DE MEMORIA Y STACK)
 * =============================================================================================== */

nmbs_error handle_read_holding_registers(uint16_t address, uint16_t quantity, uint16_t* registers_out, uint8_t unit_id, void* arg) 
{
    if (address + quantity > REGS_ADDR_MAX + 1) 
    {
        return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    }
    
    int i;
    /* Evadimos la trampa de lectura en dirección impar del buffer UART */
    uint8_t* byte_ptr_out = (uint8_t*)registers_out;
    
    for (i = 0; i < quantity; i++) 
    {
        uint16_t valor = server_registers[address + i];
        /* Asignación directa a nivel de byte. Sin memcpy = Sin uso de Stack. */
        byte_ptr_out[i * 2]     = (uint8_t)(valor & 0xFF);
        byte_ptr_out[i * 2 + 1] = (uint8_t)((valor >> 8) & 0xFF);
    }
    
    return NMBS_ERROR_NONE;
}

nmbs_error handle_write_multiple_registers(uint16_t address, uint16_t quantity, const uint16_t* registers, uint8_t unit_id, void* arg) 
{
    if (address + quantity > REGS_ADDR_MAX + 1) 
    {
        return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    }
    
    int i;
    /* Evadimos la trampa de escritura desde dirección impar del buffer UART */
    const uint8_t* byte_ptr_in = (const uint8_t*)registers; 
    
    for (i = 0; i < quantity; i++) 
    {
        uint16_t valor_seguro;
        uint8_t* ptr = (uint8_t*)&valor_seguro;
        
        /* Extracción byte a byte desde dirección impar. */
        ptr[0] = byte_ptr_in[i * 2];
        ptr[1] = byte_ptr_in[i * 2 + 1];
        
        server_registers[address + i] = valor_seguro;
    }
    
    return NMBS_ERROR_NONE;
}

nmbs_error handle_write_single_register(uint16_t address, uint16_t value, uint8_t unit_id, void* arg) 
{
    if (address > REGS_ADDR_MAX + 1) 
    {
        return NMBS_EXCEPTION_ILLEGAL_DATA_ADDRESS;
    }
    
    server_registers[address] = value;
    
    return NMBS_ERROR_NONE;
}

/* ===============================================================================================
 * FUNCIONES PÚBLICAS DE LA LIBRERÍA
 * =============================================================================================== */

void Modbus_Init(void) 
{
    config_UART2();      
    config_Timer1_1ms(); 
    
    nmbs_platform_conf_create(&platform_conf);
    platform_conf.transport = NMBS_TRANSPORT_RTU;
    platform_conf.read = read_serial;
    platform_conf.write = write_serial;
    platform_conf.arg = NULL;

    nmbs_callbacks_create(&callbacks);
    callbacks.read_holding_registers = handle_read_holding_registers;
    callbacks.write_multiple_registers = handle_write_multiple_registers;
    callbacks.write_single_register = handle_write_single_register;

    nmbs_error err = nmbs_server_create(&nmbs, RTU_SERVER_ADDRESS, &platform_conf, &callbacks);
    
    if (err != NMBS_ERROR_NONE) 
    {
        onError();
    }

    nmbs_set_read_timeout(&nmbs, 100);
    nmbs_set_byte_timeout(&nmbs, 20);
}

void Modbus_Tasks(void) 
{
    if (rx_head != rx_tail) 
    {
        nmbs_error err = nmbs_server_poll(&nmbs);
        
        if (err == NMBS_ERROR_TRANSPORT) 
        {
            onError();
        }
    }
}