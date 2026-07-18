#ifndef CONFIG
#define CONFIG
// DSPIC30F4013 Configuration Bit Settings

// FOSC: Oscilador Externo
// Cambiado de FRC_PLL16 a XT_PLL16 para usar el cristal físico de 7.3728 MHz
#pragma config FOSFPR = XT_PLL16        // Oscillator (XT w/PLL 16x)
#pragma config FCKSMEN = CSW_FSCM_OFF   // Clock Switching and Monitor (Disabled)

// FWDT: Perro Guardián (Watchdog)
// VENTANA: ~16.3 segundos antes del reinicio
#pragma config FWPSB = WDTPSB_16    // Prescaler B = 1:16
#pragma config FWPSA = WDTPSA_512   // Prescaler A = 1:512
#pragma config WDT = WDT_ON         

// FBORPOR: Protección de Energía
#pragma config FPWRT = PWRT_64          // POR Timer Value (64ms)
#pragma config BODENV = BORV27          // Brown Out Voltage (2.7V)
#pragma config BOREN = PBOR_ON          // PBOR Enable (Enabled)
#pragma config MCLRE = MCLR_EN          // Master Clear Enable (Enabled)

// FGS: Seguridad de Código
#pragma config GWRP = GWRP_OFF          // General Code Segment Write Protect (Disabled)
#pragma config GCP = CODE_PROT_OFF      // General Segment Code Protection (Disabled)

// FICD: Pines de Programación y Depuración
#pragma config ICS = ICS_PGD            // Comm Channel Select (Use PGC/EMUC and PGD/EMUD)

#include <xc.h>

#endif /* CONFIG */