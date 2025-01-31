package platform_sensors

import (
	"bufio"
	"log"
	"strconv"
	"strings"
	"time"

	"github.com/aler9/goroslib"
	"github.com/gwaxG/robot_ws/control/pkg/connections"
	"github.com/gwaxG/robot_ws/control/pkg/state"
)

// struct reading sensor information and sending it to ROS
type PlatformSensors struct {
	stopRelease chan string
	sensor      *state.Sensor
	sensorPub   *goroslib.Publisher
}

func (i *PlatformSensors) Init(stopReleaseCh chan string) {
	i.stopRelease = stopReleaseCh
	i.sensor = &state.Sensor{}
	i.sensorPub, _ = goroslib.NewPublisher(goroslib.PublisherConf{
		Node:  connections.RosConnection(),
		Topic: "robot/sensors",
		Msg:   &state.Sensor{},
	})
}

func (i *PlatformSensors) Serve() {
	go func() {
		reader := bufio.NewReader(*connections.GetConnBase())
		var str string
		for {
			ba, _, err := reader.ReadLine()
			if err != nil {
				// log.Println("Can not read from the robot")

			} else {
				str = strings.Trim(string(ba), "[]")
				i.handleSensorData(str)
			}
		}
	}()
	go func() {
		ticker := time.NewTicker(100 * time.Millisecond)
		for {
			select {
			case <-ticker.C:
				i.sensorPub.Write(i.sensor)
			}
		}
	}()
	select {}
}

func (i *PlatformSensors) handleSensorData(line string) {
	defer func() {
		if r := recover(); r != nil {
			log.Println("Recovering in PlatformSensors.handleSensorData.\nCut sensor data.")
			line = ""
		}
	}()
	if strings.HasPrefix(line, "#") {
		strArray := strings.Split(line, ",")
		i.sensor.Yaw, _ = strconv.ParseFloat(strArray[2], 64)
		i.sensor.GyroX, _ = strconv.ParseFloat(strArray[4], 64)
		i.sensor.GyroY, _ = strconv.ParseFloat(strArray[5], 64)
		i.sensor.GyroZ, _ = strconv.ParseFloat(strArray[6], 64)
		i.sensor.AccelX, _ = strconv.ParseFloat(strArray[8], 64)
		i.sensor.AccelY, _ = strconv.ParseFloat(strArray[9], 64)
		i.sensor.AccelZ, _ = strconv.ParseFloat(strArray[10], 64)
	} else if strings.HasPrefix(line, "MM") {
		// V(motorBoard data) AI(motor temp) A(current) C(position data)

		index, _ := strconv.Atoi(string(line[2]))
		line = line[4:]

		if index == 0 && strings.HasPrefix(line, "V") { // index == 2 &&
			voltageInt, _ := strconv.ParseInt(strings.Split(line[2:], ":")[1], 10, 64)
			i.sensor.Voltage = float64(voltageInt) / 10
		}
		if strings.HasPrefix(line, "C") {
			posEncoders := strings.Split(line[2:], ":")
			pos1, _ := strconv.ParseInt(posEncoders[0], 10, 64)
			pos2, _ := strconv.ParseInt(posEncoders[0], 10, 64)
			switch index {
			case 0:
				i.sensor.LeftMotorCountsFront = pos1
				i.sensor.RightMotorCountsFront = pos2
			case 1:
				i.sensor.LeftMotorCountsRear = pos1
				i.sensor.RightMotorCountsRear = pos2
			case 2:
				i.sensor.FrontFlipperCounts = pos1
				i.sensor.RearFlipperCounts = pos2
			}
		}
		if strings.HasPrefix(line, "A") {
			currents := strings.Split(line[2:], ":")
			current1, _ := strconv.ParseInt(currents[0], 10, 64)
			current2, _ := strconv.ParseInt(currents[0], 10, 64)
			/*switch index {
			case 0:
				i.sensor.FrontLeftMotorCurrent = float64(current1) / 10
				i.sensor.FrontRightMotorCurrent = float64(current2) / 10

			case 1:
				i.sensor.RearLeftMotorCurrent = float64(current1) / 10
				i.sensor.RearRightMotorCurrent = float64(current2) / 10
			case 2:
				i.sensor.FrontFlipperCurrent = float64(current1) / 10
				i.sensor.FrontFlipperCurrent = float64(current2) / 10
			}*/

			i.sensor.FrontFlipperCurrent = float64(current1) / 10
			i.sensor.FrontFlipperCurrent = float64(current2) / 10

		}
	}
}
