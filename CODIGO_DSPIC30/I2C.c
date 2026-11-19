/* 
 * File:   I2C.c
 * Author: Favio Alexander (Alex)
 */

#include "I2C.h"
#include <libpic30.h>

// Retardo para generar una frecuencia de aprox 100 kHz
#define I2C_DELAY() __delay_us(5)

void I2C_Init(void) {
    SDA_HIGH(); // Flotando en alto
    SCL_HIGH(); // Flotando en alto
}

void I2C_Start(void) {
    SDA_HIGH();
    SCL_HIGH();
    I2C_DELAY();
    SDA_LOW();  // Jala SDA a GND mientras SCL esta en alto
    I2C_DELAY();
    SCL_LOW();  // Jala SCL a GND
}

void I2C_Stop(void) {
    SDA_LOW();
    I2C_DELAY();
    SCL_HIGH(); // Libera SCL
    I2C_DELAY();
    SDA_HIGH(); // Libera SDA mientras SCL esta en alto
    I2C_DELAY();
}

// Escribe un byte y retorna el estado del ACK (0 = OK, 1 = Error/NACK)
uint8_t I2C_Write(uint8_t data) {
    uint8_t i;
    uint8_t ack;

    // Transmitir los 8 bits, MSB primero
    for(i = 0; i < 8; i++) {
        if(data & 0x80) {
            SDA_HIGH(); // Bit = 1
        } else {
            SDA_LOW();  // Bit = 0
        }
        
        I2C_DELAY();
        SCL_HIGH(); // Pulso de reloj arriba
        I2C_DELAY();
        SCL_LOW();  // Pulso de reloj abajo
        
        data <<= 1; // Desplazar al siguiente bit
    }

    // El Noveno pulso de reloj es para leer el ACK del esclavo (tu LCD)
    SDA_HIGH();     // Soltamos la linea SDA para escuchar
    I2C_DELAY();
    SCL_HIGH();     // Reloj arriba
    I2C_DELAY();
    
    ack = SDA_READ(); // Leemos qué nos respondió el LCD (0 = ACK)
    
    SCL_LOW();      // Reloj abajo, terminamos el ciclo
    
    return ack;
}