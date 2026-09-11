package main

import (
	"bytes"
	"encoding/json"
	"io"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

func readFramedJSON(input io.Reader) ([]byte, error) {
	return framing.ReadRecord(input, executorFrameLimit)
}

func frameJSONForTest(payload []byte) (*bytes.Reader, error) {
	record, err := framing.EncodeRecord(payload, executorFrameLimit)
	return bytes.NewReader(record), err
}

func readExecutorMessageForTest(input io.Reader) (executorMessage, error) {
	payload, err := readFramedJSON(input)
	if err != nil {
		return executorMessage{}, err
	}
	var message executorMessage
	err = json.Unmarshal(payload, &message)
	return message, err
}
