/* 
 * File:   I2C.h
 * Author: Favio Alexander (Alex)
 * Descripcion: Libreria Soft-I2C (Bit-Banging) para el dsPIC30F4013
 */

#ifndef I2C_SOFT_H
#define	I2C_SOFT_H

#include <xc.h>
#include <stdint.h>
#include "RELOJ.h" // Asegúrate de incluir esto para que reconozca FCY y los delays

// =========================================================================
// MACROS DE CONTROL OPEN-DRAIN (TAIJUTSU I2C)
// =========================================================================
// Para un "1", ponemos el pin como entrada (Alta impedancia). El Pull-up hace el resto.
// Para un "0", ponemos el pin como salida y el LAT en 0 (Conectamos a GND).

#define SCL_HIGH()  { TRISBbits.TRISB12 = 1; }
#define SCL_LOW()   { LATBbits.LATB12 = 0; TRISBbits.TRISB12 = 0; }

#define SDA_HIGH()  { TRISBbits.TRISB11 = 1; }
#define SDA_LOW()   { LATBbits.LATB11 = 0; TRISBbits.TRISB11 = 0; }

#define SDA_READ()  PORTBbits.RB11

// Prototipos
void I2C_Init(void);
void I2C_Start(void);
void I2C_Stop(void);
uint8_t I2C_Write(uint8_t data);

#endif	/* I2C_SOFT_H */