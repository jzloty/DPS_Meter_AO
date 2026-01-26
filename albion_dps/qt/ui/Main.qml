import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    visible: true
    width: 1120
    height: 720
    title: "Albion DPS Meter"
    color: "#0b0f14"

    property color textColor: "#e6edf3"
    property color mutedColor: "#9aa4af"
    property color accentColor: "#4aa3ff"
    property color panelColor: "#131a22"
    property color borderColor: "#1f2a37"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 12

        Rectangle {
            Layout.fillWidth: true
            height: 72
            color: panelColor
            radius: 8
            border.color: borderColor

            RowLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 20

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Text {
                        text: "Albion DPS Meter"
                        color: textColor
                        font.pixelSize: 20
                        font.bold: true
                    }
                    Text {
                        text: "Mode: " + uiState.mode + "  |  Zone: " + uiState.zone
                        color: mutedColor
                        font.pixelSize: 12
                    }
                }

                ColumnLayout {
                    spacing: 4
                    Text {
                        text: uiState.timeText
                        color: textColor
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                    }
                    Text {
                        text: "Fame: " + uiState.fameText + "  |  Fame/h: " + uiState.famePerHourText
                        color: mutedColor
                        font.pixelSize: 12
                        horizontalAlignment: Text.AlignRight
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: panelColor
                radius: 8
                border.color: borderColor

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Text {
                        text: "Scoreboard (sorted by " + uiState.sortKey + ")"
                        color: textColor
                        font.pixelSize: 14
                        font.bold: true
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 26
                        color: "#0f1620"
                        radius: 4

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 6
                            spacing: 12

                            Text { text: "Name"; color: mutedColor; font.pixelSize: 11; Layout.preferredWidth: 140 }
                            Text { text: "DMG"; color: mutedColor; font.pixelSize: 11; Layout.preferredWidth: 60 }
                            Text { text: "HEAL"; color: mutedColor; font.pixelSize: 11; Layout.preferredWidth: 60 }
                            Text { text: "DPS"; color: mutedColor; font.pixelSize: 11; Layout.preferredWidth: 60 }
                            Text { text: "HPS"; color: mutedColor; font.pixelSize: 11; Layout.preferredWidth: 60 }
                            Text { text: "BAR"; color: mutedColor; font.pixelSize: 11; Layout.fillWidth: true }
                        }
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: uiState.playersModel
                        delegate: Rectangle {
                            width: ListView.view.width
                            height: 34
                            color: "transparent"

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 12

                                Text {
                                    text: name
                                    color: "#e6edf3"
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    Layout.preferredWidth: 140
                                }
                                Text { text: damage; color: mutedColor; font.pixelSize: 12; Layout.preferredWidth: 60 }
                                Text { text: heal; color: mutedColor; font.pixelSize: 12; Layout.preferredWidth: 60 }
                                Text { text: dps.toFixed(1); color: mutedColor; font.pixelSize: 12; Layout.preferredWidth: 60 }
                                Text { text: hps.toFixed(1); color: mutedColor; font.pixelSize: 12; Layout.preferredWidth: 60 }

                                Rectangle {
                                    Layout.fillWidth: true
                                    height: 10
                                    radius: 4
                                    color: "#0f1620"
                                    border.color: "#1f2a37"
                                    Rectangle {
                                        height: parent.height
                                        width: Math.max(4, parent.width * barRatio)
                                        radius: 4
                                        color: barColor
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.preferredWidth: 360
                Layout.fillHeight: true
                color: panelColor
                radius: 8
                border.color: borderColor

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 8

                    Text {
                        text: "History"
                        color: textColor
                        font.pixelSize: 14
                        font.bold: true
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: uiState.historyModel
                        delegate: Rectangle {
                            width: ListView.view.width
                            height: 86
                            radius: 6
                            color: "#0f1620"
                            border.color: "#1f2a37"
                            border.width: 1

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 8
                                spacing: 4

                                RowLayout {
                                    Layout.fillWidth: true
                                    Text { text: label; color: textColor; font.pixelSize: 12; font.bold: true }
                                    Item { Layout.fillWidth: true }
                                    Button {
                                        text: "Copy"
                                        onClicked: uiState.copyHistory(index)
                                    }
                                }
                                Text { text: totals; color: mutedColor; font.pixelSize: 11 }
                                Text {
                                    text: players
                                    color: textColor
                                    font.pixelSize: 11
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        height: 120
                        radius: 6
                        color: "#0f1620"
                        border.color: "#1f2a37"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 8
                            spacing: 4
                            Text { text: "Legend"; color: textColor; font.pixelSize: 12; font.bold: true }
                            Text { text: "q: quit  |  b/z/m: mode  |  1-4: sort"; color: mutedColor; font.pixelSize: 11 }
                            Text { text: "space: manual start/stop  |  n: archive  |  r: fame reset"; color: mutedColor; font.pixelSize: 11 }
                            Text { text: "1-9: copy history entry"; color: mutedColor; font.pixelSize: 11 }
                        }
                    }
                }
            }
        }
    }
}
