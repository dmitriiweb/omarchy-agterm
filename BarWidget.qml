import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "dmitriiweb.agterm"
  manageIpc: false

  readonly property bool vertical: bar ? bar.vertical : false
  readonly property int barSize: bar ? bar.barSize : Style.bar.sizeHorizontal
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.55)
  readonly property string home: Quickshell.env("HOME")
  readonly property string bridgePath: String(Qt.resolvedUrl("scripts/agterm_bridge.py")).replace(/^file:\/\//, "")
  // The glyph is rasterized from the canonical agterm-grid-symbolic.svg.
  // Select the contrasting raster without requiring QtSvg or QtQuick.Effects.
  readonly property string iconOnDark: String(Qt.resolvedUrl("assets/agterm-grid-on-dark.png"))
  readonly property string iconOnLight: String(Qt.resolvedUrl("assets/agterm-grid-on-light.png"))
  readonly property bool foregroundIsLight: (foreground.r * 0.2126 + foreground.g * 0.7152 + foreground.b * 0.0722) >= 0.5
  readonly property string iconPath: foregroundIsLight ? iconOnDark : iconOnLight
  readonly property string cliPath: String(setting("cliPath", ""))
  readonly property bool showIdle: setting("showIdle", true)
  readonly property bool showIcon: vertical || setting("showIcon", true)

  property var state: ({ online: false, error: "Starting monitor", sessions: [], windowCount: 0 })
  property string focusError: ""
  property string focusStderr: ""
  readonly property bool online: state.online === true
  readonly property var sessions: state.sessions || []
  readonly property var visibleSessions: sortedVisibleSessions()
  readonly property var counts: ({
    blocked: sessions.filter(function(s) { return s.status === "blocked" }).length,
    completed: sessions.filter(function(s) { return s.status === "completed" }).length,
    active: sessions.filter(function(s) { return s.status === "active" }).length
  })
  readonly property var states: [
    { key: "blocked", title: "Blocked", color: "#e0af68" },
    { key: "completed", title: "Done", color: "#9ece6a" },
    { key: "active", title: "Working", color: "#7aa2f7" }
  ]

  visible: true
  implicitWidth: vertical ? barSize : Math.max(barSize, barContent.implicitWidth + Style.space(22))
  implicitHeight: barSize

  function colorFor(status) {
    if (status === "blocked") return "#e0af68"
    if (status === "completed") return "#9ece6a"
    if (status === "active") return "#7aa2f7"
    return "#737aa2"
  }
  function displayStatus(status) {
    if (status === "blocked") return "Blocked"
    if (status === "completed") return "Done"
    if (status === "active") return "Working"
    return "Idle"
  }
  function sortRank(status) {
    if (status === "blocked") return 0
    if (status === "completed") return 1
    if (status === "active") return 2
    return 3
  }
  function shortenPath(path) {
    if (!path) return ""
    return home && path.indexOf(home + "/") === 0 ? "~" + path.slice(home.length) : path
  }
  function tooltipText() {
    if (!online) return "agterm: " + (state.error || "offline")
    return "agterm  •  " + sessions.length + " sessions  •  " +
      counts.blocked + " blocked  •  " + counts.completed + " done  •  " + counts.active + " working"
  }
  function sortedVisibleSessions() {
    var items = showIdle ? sessions.slice() : sessions.filter(function(s) { return s.status !== "idle" })
    items.sort(function(a, b) {
      var aLast = Number(a.lastUsedAt || 0)
      var bLast = Number(b.lastUsedAt || 0)
      if (aLast > 0 || bLast > 0) {
        if (aLast !== bLast) return bLast - aLast
      }
      var aActive = a.active ? 0 : 1
      var bActive = b.active ? 0 : 1
      if (aActive !== bActive) return aActive - bActive
      var aRank = sortRank(a.status)
      var bRank = sortRank(b.status)
      if (aRank !== bRank) return aRank - bRank
      var aChanged = Number(a.changedAt || 0)
      var bChanged = Number(b.changedAt || 0)
      if (aChanged !== bChanged) return bChanged - aChanged
      return (a.name || "").localeCompare((b.name || ""))
    })
    return items
  }
  function open() { controller.show() }
  function close() { controller.hide() }
  function toggle() { if (opened) close(); else open() }
  function refresh() {
    watcher.running = false
    restartTimer.restart()
  }
  function focusSession(session) {
    if (focusProcess.running || !session || !session.windowId || !session.id) return
    focusError = ""
    focusStderr = ""
    open()
    focusProcess.command = ["python3", bridgePath, "focus", session.windowId, session.id, "--cli", cliPath]
    focusProcess.running = true
  }
  function focusAttention() {
    if (sessions.length > 0) focusSession(sessions[0])
  }

  Process {
    id: focusProcess
    command: []
    stderr: SplitParser {
      onRead: function(line) {
        root.focusStderr = (root.focusStderr + String(line)).slice(-280)
      }
    }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        root.close()
        root.refresh()
      } else {
        var detail = root.focusStderr.trim()
        root.focusError = "Could not focus session" + (detail ? ": " + detail : " (exit " + exitCode + ")")
      }
    }
  }

  Process {
    id: watcher
    command: ["python3", root.bridgePath, "watch", "--cli", root.cliPath]
    running: true
    stdout: SplitParser {
      onRead: function(line) {
        try { root.state = JSON.parse(String(line)) }
        catch (e) { root.state = { online: false, error: "Invalid monitor data", sessions: [] } }
      }
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.state = Object.assign({}, root.state, {
          eventWarning: "Monitor exited with code " + exitCode + "; restarting",
        })
      }
      restartTimer.restart()
    }
  }
  Timer {
    id: restartTimer
    interval: 1500
    repeat: false
    onTriggered: watcher.running = true
  }
  Rectangle {
    id: button
    anchors.fill: parent
    radius: Style.cornerRadius
    color: root.opened ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12) : "transparent"

    Row {
      id: barContent
      anchors.centerIn: parent
      spacing: Style.space(7)
      Image {
        visible: root.showIcon
        source: root.iconPath
        sourceSize.width: Math.max(14, Style.bar.iconFont)
        sourceSize.height: Math.max(14, Style.bar.iconFont)
        width: Math.max(14, Style.bar.iconFont)
        height: Math.max(14, Style.bar.iconFont)
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
        opacity: root.online ? 1 : 0.45
        anchors.verticalCenter: parent.verticalCenter
      }
      Rectangle {
        visible: !root.online
        width: Style.space(5)
        height: width
        radius: width / 2
        color: root.dim
        anchors.verticalCenter: parent.verticalCenter
      }
      Repeater {
        model: root.vertical ? [] : root.states
        Row {
          required property var modelData
          readonly property int count: Number(root.counts[modelData.key] || 0)
          visible: count > 0
          spacing: Style.space(3)
          anchors.verticalCenter: parent.verticalCenter
          Rectangle {
            width: Style.space(5)
            height: width
            radius: width / 2
            color: parent.modelData.color
            anchors.verticalCenter: parent.verticalCenter
          }
          Text {
            text: parent.count
            color: root.foreground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.caption
            font.bold: true
            anchors.verticalCenter: parent.verticalCenter
          }
        }
      }
    }
    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
      cursorShape: Qt.PointingHandCursor
      onEntered: if (root.bar) root.bar.showTooltip(button, root.tooltipText())
      onExited: if (root.bar) root.bar.hideTooltip(button)
      onClicked: function(mouse) {
        if (root.bar) root.bar.hideTooltip(button)
        if (mouse.button === Qt.RightButton) root.refresh()
        else if (mouse.button === Qt.MiddleButton) root.focusAttention()
        else root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: popup
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: fittedContentWidth(Style.space(370))
    contentHeight: fittedContentHeight(panelContent.implicitHeight, Style.space(640))


    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onActivateRequested: root.focusAttention()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (dy === 0) return
        var step = Math.max(Style.space(40), flickable.height * 0.75)
        var maximum = Math.max(0, flickable.contentHeight - flickable.height)
        flickable.contentY = Math.max(0, Math.min(maximum, flickable.contentY + dy * step))
      }
      Flickable {
        id: flickable
        anchors.fill: parent
        contentWidth: width
        contentHeight: panelContent.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick

        Column {
          id: panelContent
          width: parent.width
          spacing: Style.space(12)

          PanelHero {
            width: parent.width
            title: "agterm"
            meta: root.online ? (root.sessions.length + " sessions across " + root.state.windowCount + " windows") : "Waiting for agterm"
            foreground: root.foreground
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
            iconComponent: Component {
              Image {
                source: root.iconPath
                sourceSize.width: Style.font.display
                sourceSize.height: Style.font.display
                width: Style.font.display
                height: Style.font.display
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true
              }
            }
          }
          Row {
            visible: root.online
            spacing: Style.space(8)
            Repeater {
              model: root.states
              Rectangle {
                required property var modelData
                readonly property int count: Number(root.counts[modelData.key] || 0)
                width: summaryText.implicitWidth + Style.space(16)
                height: Style.space(28)
                radius: height / 2
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06)
                border.width: 1
                border.color: modelData.color
                Text {
                  id: summaryText
                  anchors.centerIn: parent
                  text: parent.count + " " + parent.modelData.title
                  color: parent.modelData.color
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }
          PanelSeparator {
            foreground: root.foreground
          }
          Column {
            width: parent.width
            spacing: Style.space(7)
            Repeater {
              model: root.visibleSessions
              Rectangle {
                id: item
                required property var modelData
                width: parent.width
                height: Style.space(66)
                radius: Style.cornerRadius
                color: hover.containsMouse
                  ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
                  : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.05)

                Rectangle {
                  width: Style.space(4)
                  radius: width / 2
                  anchors.left: parent.left
                  anchors.top: parent.top
                  anchors.bottom: parent.bottom
                  anchors.margins: Style.space(10)
                  color: root.colorFor(item.modelData.status)
                }
                Column {
                  anchors.left: parent.left
                  anchors.leftMargin: Style.space(16)
                  anchors.right: statusText.left
                  anchors.rightMargin: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(5)
                  Text {
                    width: parent.width
                    text: item.modelData.name + (item.modelData.active ? "  •" : "")
                    color: root.foreground
                    font.family: root.bar ? root.bar.fontFamily : Style.font.family
                    font.pixelSize: Style.font.body
                    font.bold: item.modelData.status !== "idle"
                    elide: Text.ElideRight
                  }
                  Text {
                    width: parent.width
                    text: item.modelData.workspace + "  •  " + root.shortenPath(item.modelData.cwd)
                    color: root.dim
                    font.family: root.bar ? root.bar.fontFamily : Style.font.family
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideMiddle
                  }
                }
                Text {
                  id: statusText
                  anchors.right: parent.right
                  anchors.rightMargin: Style.space(12)
                  anchors.verticalCenter: parent.verticalCenter
                  text: root.displayStatus(item.modelData.status)
                  color: root.colorFor(item.modelData.status)
                  font.family: root.bar ? root.bar.fontFamily : Style.font.family
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  id: hover
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.focusSession(item.modelData)
                }
              }
            }
          }
          Text {
            visible: !!root.focusError
            width: parent.width
            text: root.focusError
            color: "#f7768e"
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WrapAnywhere
          }
          Text {
            visible: !!(root.state.warning || root.state.eventWarning)
            width: parent.width
            text: [root.state.warning, root.state.eventWarning].filter(function(value) { return !!value }).join(" · ")
            color: root.dim
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WrapAnywhere
          }
          Text {
            visible: root.online && root.visibleSessions.length === 0
            width: parent.width
            topPadding: Style.space(22)
            bottomPadding: Style.space(22)
            text: "No sessions to display"
            color: root.dim
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
            horizontalAlignment: Text.AlignHCenter
          }
          Text {
            visible: !root.online
            width: parent.width
            text: root.state.error || "Start agterm-linux to reconnect automatically"
            color: root.dim
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WrapAnywhere
          }
          Text {
            visible: root.visibleSessions.length > 0
            width: parent.width
            text: "Click a session to focus  ·  Enter jumps to attention"
            color: root.dim
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }
}
