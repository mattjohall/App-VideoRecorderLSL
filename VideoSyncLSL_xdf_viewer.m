function VideoSyncLSL_xdf_viewer(recordingLocation)
% VideoSyncLSL_xdf_viewer Load an XDF recording and inspect synced video/events.
%
% Usage:
%   VideoSyncLSL_xdf_viewer
%   VideoSyncLSL_xdf_viewer('C:\path\to\recording.xdf')
%
% Requirements:
%   - load_xdf.m must be on the MATLAB path
%   - The XDF should include the VideoSyncLSLStatus stream
%   - The saved video file path should still be valid, or its sidecar JSON/video
%     should still exist next to the XDF recording

if nargin < 1 || strlength(string(recordingLocation)) == 0
    [fileName, folderName] = uigetfile({'*.xdf', 'XDF files (*.xdf)'}, 'Select XDF recording');
    if isequal(fileName, 0)
        return;
    end
    recordingLocation = fullfile(folderName, fileName);
end

recordingLocation = char(recordingLocation);
assert(exist(recordingLocation, 'file') == 2, 'Recording not found: %s', recordingLocation);
assert(exist('load_xdf', 'file') == 2, 'load_xdf.m must be on the MATLAB path.');

streams = load_xdf(recordingLocation);
session = buildSession(streams, recordingLocation);
state = buildViewerState(session);
createViewerUi(state);

end

function session = buildSession(streams, recordingLocation)
statusStream = [];
for idx = 1:numel(streams)
    if isfield(streams{idx}.info, 'name') && strcmp(streams{idx}.info.name, 'VideoSyncLSLStatus')
        statusStream = streams{idx};
        break;
    end
end
assert(~isempty(statusStream), 'Could not find VideoSyncLSLStatus in the XDF.');

[videoPath, onsetTime, offsetTime] = parseStatusStream(statusStream, recordingLocation);
videoReader = VideoReader(videoPath);
durationSec = videoReader.Duration;
fps = videoReader.FrameRate;
frameCount = max(1, floor(durationSec * fps));

[audioSamples, audioRate] = tryLoadAudio(videoPath);
events = collectEvents(streams, onsetTime, durationSec);

session = struct( ...
    'streams', {streams}, ...
    'recordingLocation', recordingLocation, ...
    'videoPath', videoPath, ...
    'videoReader', videoReader, ...
    'fps', fps, ...
    'durationSec', durationSec, ...
    'frameCount', frameCount, ...
    'onsetTime', onsetTime, ...
    'offsetTime', offsetTime, ...
    'audioSamples', audioSamples, ...
    'audioRate', audioRate, ...
    'events', {events});
end

function [videoPath, onsetTime, offsetTime] = parseStatusStream(statusStream, recordingLocation)
samples = statusStream.time_series;
times = statusStream.time_stamps;
assert(~isempty(samples), 'VideoSyncLSLStatus exists but contains no samples.');

recordingOnIdx = [];
recordingOffIdx = [];
videoPath = '';

for idx = 1:numel(samples)
    message = string(samples{idx});
    fields = parseKeyValueMessage(message);
    if isfield(fields, 'recording')
        if strcmp(strtrim(fields.recording), '1') && isempty(recordingOnIdx)
            recordingOnIdx = idx;
            if isfield(fields, 'filename')
                candidate = strtrim(fields.filename);
                if exist(candidate, 'file') == 2
                    videoPath = candidate;
                end
            end
        elseif strcmp(strtrim(fields.recording), '0')
            recordingOffIdx = idx;
        end
    end
end

assert(~isempty(recordingOnIdx), 'Could not find recording onset in VideoSyncLSLStatus.');
onsetTime = times(recordingOnIdx);
if isempty(recordingOffIdx)
    offsetTime = times(end);
else
    offsetTime = times(recordingOffIdx);
end

if isempty(videoPath)
    xdfFolder = fileparts(recordingLocation);
    aviFiles = dir(fullfile(xdfFolder, '*.avi'));
    assert(~isempty(aviFiles), 'Could not resolve video file from the XDF or folder.');
    [~, newestIdx] = max([aviFiles.datenum]);
    videoPath = fullfile(aviFiles(newestIdx).folder, aviFiles(newestIdx).name);
end
end

function [audioSamples, audioRate] = tryLoadAudio(videoPath)
audioSamples = [];
audioRate = [];
try
    [audioSamples, audioRate] = audioread(videoPath);
catch
    audioSamples = [];
    audioRate = [];
end
end

function events = collectEvents(streams, onsetTime, durationSec)
events = struct('label', {}, 'streamName', {}, 'xdfTime', {}, 'videoTime', {}, 'rawValue', {});
for idx = 1:numel(streams)
    stream = streams{idx};
    if ~isfield(stream, 'time_stamps') || isempty(stream.time_stamps)
        continue;
    end
    streamName = getStreamName(stream);
    if strcmp(streamName, 'VideoSyncLSLStatus')
        continue;
    end
    series = stream.time_series;
    if isnumeric(series)
        series = num2cell(series, 1);
    end
    if ~iscell(series)
        continue;
    end
    for sampleIdx = 1:min(numel(series), numel(stream.time_stamps))
        videoTime = stream.time_stamps(sampleIdx) - onsetTime;
        if videoTime < 0 || videoTime > durationSec
            continue;
        end
        rawValue = series{sampleIdx};
        label = buildEventLabel(streamName, rawValue, videoTime);
        events(end + 1) = struct( ... %#ok<AGROW>
            'label', label, ...
            'streamName', streamName, ...
            'xdfTime', stream.time_stamps(sampleIdx), ...
            'videoTime', videoTime, ...
            'rawValue', rawValue);
    end
end

[~, sortIdx] = sort([events.videoTime]);
events = events(sortIdx);
end

function label = buildEventLabel(streamName, rawValue, videoTime)
if isnumeric(rawValue)
    valueText = mat2str(rawValue);
elseif isstring(rawValue) || ischar(rawValue)
    valueText = char(string(rawValue));
else
    valueText = '<complex sample>';
end
valueText = strrep(valueText, newline, ' ');
valueText = strtrim(valueText);
if strlength(string(valueText)) > 60
    valueText = extractBefore(string(valueText), 58) + "...";
end
label = sprintf('[%8.3fs] %s | %s', videoTime, streamName, char(valueText));
end

function streamName = getStreamName(stream)
streamName = 'UnnamedStream';
if isfield(stream, 'info') && isfield(stream.info, 'name')
    streamName = stream.info.name;
end
end

function fields = parseKeyValueMessage(message)
parts = split(string(message), ';');
fields = struct();
for idx = 1:numel(parts)
    piece = strtrim(parts(idx));
    if ~contains(piece, ':')
        continue;
    end
    kv = split(piece, ':', 2);
    key = matlab.lang.makeValidName(lower(strtrim(kv(1))));
    value = strtrim(kv(2));
    fields.(key) = char(value);
end
end

function state = buildViewerState(session)
state = struct();
state.session = session;
state.currentFrame = 1;
state.currentTime = 0;
state.stepFrames = 1;
state.isPlaying = false;
state.player = [];
state.timer = [];
state.controls = struct();
end

function createViewerUi(state)
fig = uifigure('Name', 'VideoSyncLSL XDF Viewer', 'Position', [100 100 1280 760], ...
    'KeyPressFcn', @(src, evt) onKeyPress(src, evt));
fig.CloseRequestFcn = @(src, evt) onClose(src);

grid = uigridlayout(fig, [3 3]);
grid.RowHeight = {'1x', 32, 110};
grid.ColumnWidth = {300, '1x', 220};

eventList = uilistbox(grid, 'Items', {state.session.events.label}, ...
    'ValueChangedFcn', @(src, evt) onEventSelected(src));
eventList.Layout.Row = [1 3];
eventList.Layout.Column = 1;

videoAxes = uiaxes(grid);
videoAxes.Layout.Row = 1;
videoAxes.Layout.Column = [2 3];
videoAxes.XTick = [];
videoAxes.YTick = [];
videoAxes.Box = 'on';
[~, videoName, videoExt] = fileparts(state.session.videoPath);
title(videoAxes, [videoName videoExt]);

controlGrid = uigridlayout(grid, [1 7]);
controlGrid.Layout.Row = 2;
controlGrid.Layout.Column = [2 3];
controlGrid.ColumnWidth = {100, 100, 80, 80, 80, '1x', 120};

playButton = uibutton(controlGrid, 'Text', 'Play', 'ButtonPushedFcn', @(src, evt) onPlayPause(src));
pauseAtEvent = uilabel(controlGrid, 'Text', 'Select an event to jump there paused.');
stepBack = uibutton(controlGrid, 'Text', '< Frame', 'ButtonPushedFcn', @(src, evt) stepFramesWithFigure(fig, -1));
stepForward = uibutton(controlGrid, 'Text', 'Frame >', 'ButtonPushedFcn', @(src, evt) stepFramesWithFigure(fig, 1));
stepField = uieditfield(controlGrid, 'numeric', 'Value', 1, 'Limits', [1 Inf], ...
    'RoundFractionalValues', true, 'ValueChangedFcn', @(src, evt) onStepChanged(src));
timeField = uieditfield(controlGrid, 'text', 'Editable', 'off');
frameField = uieditfield(controlGrid, 'text', 'Editable', 'off');

infoArea = uitextarea(grid, 'Editable', 'off');
infoArea.Layout.Row = 3;
infoArea.Layout.Column = [2 3];

state.controls.figure = fig;
state.controls.eventList = eventList;
state.controls.videoAxes = videoAxes;
state.controls.playButton = playButton;
state.controls.pauseLabel = pauseAtEvent;
state.controls.stepBack = stepBack;
state.controls.stepForward = stepForward;
state.controls.stepField = stepField;
state.controls.timeField = timeField;
state.controls.frameField = frameField;
state.controls.infoArea = infoArea;

state.timer = timer( ...
    'ExecutionMode', 'fixedSpacing', ...
    'Period', max(1 / max(state.session.fps, 1), 0.02), ...
    'TimerFcn', @(~, ~) onTimer(fig));

fig.UserData = state;
renderCurrentFrame(fig);
end

function onEventSelected(listBox)
fig = ancestor(listBox, 'figure');
state = fig.UserData;
eventIdx = find(strcmp(listBox.Items, listBox.Value), 1);
if isempty(eventIdx) || eventIdx < 1
    return;
end
pausePlayback(fig);
eventData = state.session.events(eventIdx);
frame = timeToFrame(state.session, eventData.videoTime);
state.currentFrame = frame;
state.currentTime = frameToTime(state.session, frame);
fig.UserData = state;
renderCurrentFrame(fig);
end

function onPlayPause(button)
fig = ancestor(button, 'figure');
state = fig.UserData;
if state.isPlaying
    pausePlayback(fig);
else
    startPlayback(fig);
end
end

function startPlayback(fig)
state = fig.UserData;
if state.isPlaying
    return;
end
state.isPlaying = true;
fig.UserData = state;
if ~isempty(state.session.audioSamples)
    startAudio(fig);
end
start(state.timer);
updateControls(fig);
end

function pausePlayback(fig)
state = fig.UserData;
if isvalid(state.timer) && strcmp(state.timer.Running, 'on')
    stop(state.timer);
end
stopAudio(state);
state.isPlaying = false;
fig.UserData = state;
updateControls(fig);
end

function startAudio(fig)
state = fig.UserData;
stopAudio(state);
startSample = max(1, floor(state.currentTime * state.session.audioRate) + 1);
audioData = state.session.audioSamples(startSample:end, :);
if isempty(audioData)
    return;
end
state.player = audioplayer(audioData, state.session.audioRate);
play(state.player);
fig.UserData = state;
end

function stopAudio(state)
if ~isempty(state.player)
    try
        stop(state.player);
    catch
    end
end
end

function onTimer(fig)
if ~isvalid(fig)
    return;
end
state = fig.UserData;
if ~state.isPlaying
    return;
end
nextFrame = state.currentFrame + 1;
if nextFrame > state.session.frameCount
    pausePlayback(fig);
    return;
end
state.currentFrame = nextFrame;
state.currentTime = frameToTime(state.session, nextFrame);
fig.UserData = state;
renderCurrentFrame(fig);
end

function onStepChanged(field)
fig = ancestor(field, 'figure');
state = fig.UserData;
state.stepFrames = max(1, round(field.Value));
field.Value = state.stepFrames;
fig.UserData = state;
end

function onKeyPress(fig, evt)
switch evt.Key
    case 'rightarrow'
        stepFramesWithFigure(fig, 1);
    case 'leftarrow'
        stepFramesWithFigure(fig, -1);
    case 'space'
        onPlayPause(fig.UserData.controls.playButton);
end
end

function stepFramesWithFigure(fig, direction)
pausePlayback(fig);
state = fig.UserData;
delta = direction * max(1, round(state.stepFrames));
state.currentFrame = min(max(1, state.currentFrame + delta), state.session.frameCount);
state.currentTime = frameToTime(state.session, state.currentFrame);
fig.UserData = state;
renderCurrentFrame(fig);
end

function renderCurrentFrame(fig)
state = fig.UserData;
reader = state.session.videoReader;
reader.CurrentTime = max(0, min(state.currentTime, max(0, state.session.durationSec - 1 / max(state.session.fps, 1))));
frame = readFrame(reader);
imshow(frame, 'Parent', state.controls.videoAxes);
state.session.videoReader = reader;
fig.UserData = state;
updateControls(fig);
updateInfo(fig);
end

function updateControls(fig)
state = fig.UserData;
if state.isPlaying
    state.controls.playButton.Text = 'Pause';
else
    state.controls.playButton.Text = 'Play';
end
state.controls.timeField.Value = sprintf('Time: %.3fs', state.currentTime);
state.controls.frameField.Value = sprintf('Frame: %d / %d', state.currentFrame, state.session.frameCount);
end

function updateInfo(fig)
state = fig.UserData;
currentEventIdx = findNearestEventIdx(state.session.events, state.currentTime);
if isempty(currentEventIdx)
    eventText = 'Nearest event: none';
else
    eventData = state.session.events(currentEventIdx);
    eventText = sprintf('Nearest event: %s', eventData.label);
end
audioText = 'Audio: unavailable from video file';
if ~isempty(state.session.audioSamples)
    audioText = 'Audio: loaded and synced to play/pause';
end
state.controls.infoArea.Value = {
    ['XDF: ' state.session.recordingLocation]
    ['Video: ' state.session.videoPath]
    sprintf('Recording onset in XDF: %.6f', state.session.onsetTime)
    sprintf('Recording offset in XDF: %.6f', state.session.offsetTime)
    sprintf('Step size: %d frame(s)', max(1, state.stepFrames))
    audioText
    eventText
    'Keys: Left/Right arrow = step, Space = play/pause'
    'Selecting an event jumps there paused.'
    };
end

function idx = findNearestEventIdx(events, currentTime)
if isempty(events)
    idx = [];
    return;
end
[~, idx] = min(abs([events.videoTime] - currentTime));
end

function frame = timeToFrame(session, timeSec)
frame = min(max(1, round(timeSec * session.fps) + 1), session.frameCount);
end

function timeSec = frameToTime(session, frame)
timeSec = max(0, (frame - 1) / session.fps);
end

function onClose(fig)
state = fig.UserData;
try
    pausePlayback(fig);
catch
end
try
    if ~isempty(state.timer) && isvalid(state.timer)
        delete(state.timer);
    end
catch
end
delete(fig);
end
