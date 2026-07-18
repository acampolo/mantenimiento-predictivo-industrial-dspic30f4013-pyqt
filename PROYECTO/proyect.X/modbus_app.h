#ifndef MODBUS_APP_H
#define MODBUS_APP_H

#include <xc.h>
#include <stdint.h>
#include <libpic30.h>
#include "nanomodbus.h" 

#define REGS_ADDR_MAX 160 // 4 base + 64 Corriente + 64 Vibración

// ELIMINAMOS 'extern nmbs_bitfield server_coils;'
extern uint16_t server_registers[REGS_ADDR_MAX + 1];

void config_UART2(void);
void config_Timer2_1ms(void);
void Modbus_Init(void);
void Modbus_Tasks(void);
uint32_t get_current_time_ms(void);

#endif /* MODBUS_APP_H */