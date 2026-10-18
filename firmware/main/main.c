#include <stdio.h>
#include <stdint.h>
#include <inttypes.h>
#include "driver/i2c_master.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_err.h"

static i2c_master_dev_handle_t sensor;

static esp_err_t read_reg(uint8_t reg, uint8_t *data, size_t len) {
    return i2c_master_transmit_receive(sensor, &reg, 1, data, len, 100);
}

static esp_err_t write_reg(uint8_t reg, uint8_t value) {
    uint8_t bytes[] = {reg, value};
    return i2c_master_transmit(sensor, bytes, sizeof(bytes), 100);
}

static esp_err_t setup_sensor(void) {
    uint8_t id = 0;
    esp_err_t err = read_reg(0xff, &id, 1);
    if (err != ESP_OK) return err;
    if (id != 0x15) return ESP_ERR_NOT_FOUND;
    err = write_reg(0x09, 0x40); // software reset
    if (err != ESP_OK) return err;
    vTaskDelay(pdMS_TO_TICKS(100));
    // No averaging, no FIFO rollover; two LEDs; ADC 4096 nA, 100 Hz, 411 us.
    const uint8_t config[][2] = {
        {0x02, 0x00}, {0x03, 0x00}, {0x08, 0x0f}, {0x0a, 0x27},
        {0x0c, 0x24}, {0x0d, 0x24}, // RED / IR current approx. 7.2 mA
        {0x04, 0}, {0x05, 0}, {0x06, 0}, {0x09, 0x03}
    };
    for (unsigned i = 0; i < sizeof(config) / sizeof(config[0]); i++) {
        err = write_reg(config[i][0], config[i][1]);
        if (err != ESP_OK) return err;
    }
    return ESP_OK;
}

void app_main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    i2c_master_bus_config_t bus_config = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = CONFIG_PPG_SDA_GPIO,
        .scl_io_num = CONFIG_PPG_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus;
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &bus));
    i2c_device_config_t dev = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = 0x57,
        .scl_speed_hz = 100000,
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(bus, &dev, &sensor));
    uint64_t sequence = 0;
    while (true) {
        esp_err_t err = setup_sensor();
        if (err != ESP_OK) {
            printf("ERR,SENSOR_NOT_READY,%s\n", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }
        printf("INFO,PPG_V1,100HZ,RED_IR\n");
        while (true) {
            uint8_t pointers[3];
            err = read_reg(0x04, pointers, 3); // write pointer, overflow, read pointer
            if (err != ESP_OK) break;
            if (pointers[1] != 0) {
                printf("ERR,FIFO_OVERFLOW,RESTART\n");
                sequence += 32; // explicit discontinuity; do not invent lost samples
                break;
            }
            int count = (pointers[0] - pointers[2]) & 0x1f;
            for (int i = 0; i < count; i++) {
                uint8_t bytes[6];
                err = read_reg(0x07, bytes, sizeof(bytes));
                if (err != ESP_OK) break;
                uint32_t red = (((uint32_t)bytes[0] << 16) | ((uint32_t)bytes[1] << 8) | bytes[2]) & 0x3ffff;
                uint32_t ir = (((uint32_t)bytes[3] << 16) | ((uint32_t)bytes[4] << 8) | bytes[5]) & 0x3ffff;
                // Nominal sensor sample clock; USB arrival time is deliberately not used.
                printf("PPG,%" PRIu64 ",%" PRIu64 ",%" PRIu32 ",%" PRIu32 "\n", sequence, sequence * 10, red, ir);
                sequence++;
            }
            if (err != ESP_OK) break;
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        printf("ERR,ACQUISITION_RESTART,%s\n", esp_err_to_name(err));
        sequence += 32;
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
