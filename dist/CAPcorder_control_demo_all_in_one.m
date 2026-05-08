%% instantiate the library
disp('Loading library...');
lib = lsl_loadlib();

%% make a new stream outlet for a simple EEG demo
disp('Creating a new EEG streaminfo...');
info = lsl_streaminfo(lib, 'BioSemi', 'EEG', 8, 100, 'cf_float32', 'sdfwerr32432');

disp('Opening EEG outlet...');
eegOutlet = lsl_outlet(info);

% send data into the outlet, sample by sample
disp('Now transmitting dummy EEG data...');
for k = 1:1000
    eegOutlet.push_sample(randn(8, 1));
    pause(0.01);
end

%% start stop the camera using the CAPcorder control stream
disp('Creating CAPcorder control streaminfo...');
info = lsl_streaminfo(lib, 'CAPcorderControl_Matlab', 'videocontrol', 1, 0, 'cf_string', 'matlab-CAPcorder-control-demo');

controlOutlet = lsl_outlet(info);
pause(3);
filename = 'matlab_demo';

width = 320;
height = 240;
fps = 30;
timestamp = lsl_local_clock(lib);
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
savedir = 'C\Users\CNEL_Vega\Documents\CurrentStudy'; 
%%%%% NOTE THAT U CANT USE A COLON FOR THE DRIVE LETTER so the dict can parse!!!!

command = sprintf(['action: start; filename: %s; timestamp: %.6f; ' ...
    'width: %d; height: %d; fps: %d; frame_number: 1; output_dir: %s'], ...
    filename, timestamp, width, height, fps, savedir);

disp('Sending START command...');
disp(command);
controlOutlet.push_sample({command});

% do something here while the camera records
pause(10);

% stop
command = sprintf('action: stop; filename: %s; timestamp: %.6f', filename, lsl_local_clock(lib));

disp('Sending STOP command...');
disp(command);
controlOutlet.push_sample({command});


