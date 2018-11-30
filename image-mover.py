__author__ = 'tkraus-m'

import requests
import getopt, sys
import subprocess
import time
import json
import os
import fileinput
import shlex
import shutil
import re

# Set the Target for Docker Iamges. Valid options are 'quay' and 'docker_registry'
docker_target = 'docker_registry'
# Set the Target for HTTP Artifacts including the actual Universe JSON definition itself
# Valid options are 'nginx' and 'nexus'
http_target = 'nexus'
remove_images = False  # Will remove local copies of images already transferred to dst_registry_host

# Section below used for Source & Destination Registries
src_registry_proto = 'http://'
src_http_protocol = 'http://'
src_insecure = True
# Section below used for Destination Quay or Registry Server for Universe Docker Images
dst_registry_proto = 'http://'
dst_registry_namespace = 'image-mover/'  # Include Trailing Slash after namespace
pulled_images = []

# Update section below if a proxy exists between server where script is run and destination HTTP server
# IF proxy exists between server where script runs & Quay/registry svr then setup a Docker Daemon proxy also
http_proxy = ''
https_proxy = http_proxy
proxies = {"http": http_proxy, "https": https_proxy}

# Section below used for Destination HTTP/FRS/Artifactory for Universe Artifacts
dst_http_protocol = 'https://'
dst_http_host = 'http-artifact-svr.domain.net'
dst_http_namespace = 'maven/content/sites/GCP-SITE/DCOS-Universe/Prod/'
dst_http_repository_user = 'xyzxyzxyz'
dst_http_repository_pass = 'xyzxyzxyz'
universe_json_file = 'repo-up-to-1.8.json'
working_directory = '/tmp/'
http_artifacts_scan_zip_dir = '/var/lib/a_ansible/'


def start_universe(universe_image, command):
    print('--Starting Mesosphere/Universe Docker Image ' + universe_image)
    subprocess.Popen(command).wait()
    print('--Successfully Started Mesosphere/Universe Docker Image ' + universe_image)
    print('--Waiting 5 Seconds for Container Startup')
    time.sleep(5)


def docker_login(dst_registry_proto, dst_registry_host):
    print('--Docker Logging in for Quay Server: ' + dst_registry_host)
    command = ['sudo', 'docker', 'login', '{}{}'.format(dst_registry_proto, dst_registry_host)]
    subprocess.check_call(command)


def get_registry_images(registry_proto, registry_host):
    print("--Found Repositories on server")
    response = requests.get(registry_proto + registry_host + '/v2/_catalog', verify=False)

    if response.status_code != 200:
        print(str(response.status_code) + " Registry API CAll unsuccessful to " + registry_host)
        print("----Raw Docker Error Message is  " + response.text)
        exit(1)
    else:
        responseJson = response.json()
        if responseJson['repositories'] == []:
            print("----No Repositories found on Source Docker Registry")
            sys.exit(1)
        else:
            repositories = []
            for i in responseJson['repositories']:
                print("----Found an image named " + i)
                repositories.append(i)
            return repositories


def get_registry_manifests(registry_proto, registry_host, repos):
    registry_manifest_dict = {}
    print("--Getting Source Registry Manifests")
    for i in repos:
        response = requests.get(registry_proto + registry_host + '/v2/' + i + '/tags/list', verify=False)
        responseJson = response.json()
        print("----Manifests Response " + str(responseJson))
        name = responseJson['name']
        for tag in responseJson['tags']:
            tag = tag
            registry_manifest_dict[str(name)] = str(tag)
            print("----Name is " + name + " and tag is " + tag)
    return registry_manifest_dict


def pull_images(name):
    print('--Pulling docker image: {}'.format(name))
    command = ['docker', 'pull', name]
    subprocess.check_call(command)


def new_format_image_name(dst_registry_host, dst_registry_namespace, image, imagetag):
    print("Src Imagename is " + image)
    if '/' in image:
        newimage = '{}/{}{}:{}'.format(dst_registry_host, dst_registry_namespace, image.split("/")[1], imagetag)
        print("Slash in image name, New image is " + newimage)
        return newimage
    else:
        newimage = '{}/{}{}:{}'.format(dst_registry_host, dst_registry_namespace, image, imagetag)
        print("No slash in image so New image is " + image)
        return newimage


def tag_images(image, imagetag, fullImageId, dst_registry_host):
    new_image_name = new_format_image_name(dst_registry_host, dst_registry_namespace, image, imagetag)
    print("--Tagging temp Universe Image " + fullImageId + " for new Registry " + new_image_name)
    command = ['docker', 'tag', fullImageId, new_image_name]
    subprocess.check_call(command)
    return new_image_name


def push_images(new_image, docker_target):
    if docker_target == 'docker_registry':
        print("--Pushing Image to Docker Registry - " + new_image)
        command = ['docker', 'push', new_image]
        subprocess.check_call(command)
    if docker_target == 'quay':
        print("--Pushing Image to Quay - " + new_image)
        command = ['docker', 'push', new_image]
        subprocess.check_call(command)


def make_repo_public(new_image, dst_registry_proto):
    hostname, namespace, image = new_image.split('/')
    image_only = image.split(':')[0]
    quay_repository = '{}{}/{}/{}/{}/{}'.format(dst_registry_proto, hostname, 'api/v1/repository', namespace,
                                                image_only, 'changevisibility')
    headers = {'Authorization': 'Bearer TzOMaqr7MwFm32DcDrEDZ5o0ZlO5YQV2t4CaJOFd', 'Content-type': 'application/json'}
    print("Making Quay Repository Public for " + new_image + ' using protocol ' + dst_registry_proto)
    print("Quay Repository = " + quay_repository)
    json_data = json.dumps({'visibility': 'public'})
    response = requests.post(quay_repository, data=json_data, proxies=proxies, headers=headers, verify=False)

    if response.status_code != 200:
        print("  " + str(response.status_code) + " -- Quay API Call Failed")
        print("  " + response.text)
        print(response.raise_for_status())
        return
    else:
        print("  " + str(response.status_code) + " -- Quay API Call Success !!")
        return


def copy_http_data(working_directory, universe_json_file):
    print("--Copying Universe HTTP to hosts Working Directory ")
    command = ['sudo', 'docker', 'cp', 'temp-universe-sync:/usr/share/nginx/html/', working_directory]
    subprocess.check_output(command)
    if mode != 'test':
        command = ['sudo', 'chown', '-R', 'a_ansible:users', working_directory]
        subprocess.check_output(command)
    updated_universe_json_file = (working_directory + 'html/' + universe_json_file)
    return updated_universe_json_file  # Return updated reference to the now modified Universe.json file


def transform_json(src_string, dst_string, json_file):
    print("transform_json function is changing <" + src_string + "> with <" + dst_string + ">.")
    for line in fileinput.input(json_file, inplace=True):
        # the comma after each print statement is needed to avoid double line breaks
        print(line.replace(src_string, dst_string), )


def new_transform_json(src_string, dst_string, packages):
    for package in packages:
        for key, value in package.items():
            if key == 'resource' or key == 'config':
                print('\n Old Value is {}'.format(value))
                stringvalue = str(value)
                if src_string in stringvalue:
                    print("Found the " + src_string)
                    new_string = stringvalue.replace(src_string, dst_string)
                    print("\n New String Value = " + new_string)
                    package[key] = new_string
    return packages

    '''
    print("\n new_transform_json function is changing <"+ src_string + "> with <"+dst_string +">.")
    if src_string in content:
        print(" FOUND String, Changing "+ src_string +" to "+ dst_string +"\n")
        content.replace(src_string, dst_string)
        return content
    else:
        print("  --- *** ERROR *** --- "+ src_string + " not found in \n")
        return content
    '''


def newer_transform_json(old_new_image_dict, json_file):
    # This not working either
    for fullImageId, new_image in old_new_image_dict.items():
        print(str(fullImageId))
        print(str(new_image))
        print("newer_transform_json function is changing <" + fullImageId + "> with <" + new_image + ">.")
        for line in fileinput.input(json_file, inplace=True):
            # the comma after each print statement is needed to avoid double line breaks
            print(line.rstrip().replace(str(fullImageId), str(new_image)), )


def newest_transform_json(old_new_image_dict, json_file):
    file_handle = open(json_file, 'rb')
    file_string = file_handle.read().decode('utf-8')
    file_handle.close()

    for fullImageId, new_image in old_new_image_dict.items():
        # THIS APPEARS TO BE REDUNDANT aND THE short versions below will CATCH ALL !!!
        print("newest_transform_json function is changing " + fullImageId + " with " + new_image)
        file_string = re.sub(fullImageId, new_image, file_string)

        print(" \n newest_transform_json is replacing Image references where authors did not include a Docker TAG")
        short_fullImageId = ":".join(fullImageId.split(":")[0:2])
        short_new_image = "".join(new_image.split(":")[0])
        print("newest_transform_json function is changing " + short_fullImageId + " with " + short_new_image)
        file_string = re.sub(short_fullImageId, short_new_image, file_string)

    print("***** DEBUG ***** Updated json = " + file_string)
    # LEFT OFF HERE _ EVERYTHING WORKING EXCEPT WRITING THE FILE OUT

    file_handle = open(json_file, 'w')
    file_handle.write(file_string)
    file_handle.close()


def return_http_artifacts(working_directory):
    http_artifacts = []
    os.chdir('{}{}/'.format(working_directory, 'html'))
    for subdir, dirs, files in os.walk('{}{}'.format(working_directory, 'html')):
        for file in files:
            if file.startswith(".") or file.startswith("index.html") or file.startswith("domain.crt"):
                print("Found files to skip = " + file)

            else:
                print("Files are " + os.path.join(subdir, file))
                http_artifacts.append(os.path.join(subdir, file))
    return http_artifacts


def upload_http_nexus(dst_http_protocol, dst_http_host, dst_http_namespace, http_artifacts):
    baseurl = '{}{}/{}{}/'.format(dst_http_protocol, dst_http_host, dst_http_namespace, time.strftime("%Y-%m-%d"))
    try:
        for file in http_artifacts:
            print('\nWorking on file ' + file)
            upload_file = {'file': open(file, 'rb')}
            pathurl = (file.split("html/")[1])
            url = '{}{}'.format(baseurl, pathurl)
            print(' Uploading file to {}{}'.format(baseurl, pathurl))

            headers = {'Connection': 'keep-alive', 'content-type': 'multipart/form-data'}
            with open(file, 'rb') as uploadfile:
                response = requests.put(url, data=uploadfile, auth=(dst_http_repository_user, dst_http_repository_pass),
                                        proxies=proxies, headers=headers)

            if response.status_code != 201:
                print("  " + str(response.status_code) + " -- Nexus API CAll unsuccessful")
                print(response.raise_for_status())
                exit(1)
            else:
                print("  " + str(response.status_code) + " -- Nexus API CAll SUCCESS")
        return baseurl
    except:
        print(" **** WARNING - MISSING HTTP ARTIFACTS ****")
        return baseurl



if __name__ == "__main__":

    script_dir = os.getcwd()
    try:
        opts, args = getopt.getopt(sys.argv[1:], "s:m:d:h", ["source-registry=", "mode=", "destination-registry="])
    except getopt.GetoptError as err:
        print(err)  # will print something like "option -a not recognized"
        print('image-move.py -s <source-registry> -m <download/sync> -d <destination-registry> -l <user:password>')
        sys.exit(2)
    found_s = False
    found_m = False
    found_d = False
    found_l = False
    for opt, arg in opts:
        if opt == '-h':
            print('image-move.py -s <source-registry> -m <download/sync> -d <destination-registry>')
            print('   <download/sync> choose \'download\' to just download docker images locally')
            print('   <download/sync> choose \'sync\' to fully synchronize Universe Docker Images targets.')
            sys.exit()
        elif opt in ("-s", "--source-registry"):
            src_registry_host = arg
            found_s = True
        elif opt in ("-m", "--mode"):
            mode = arg
            found_m = True
        elif opt in ("-d", "--destination-registry"):
            dst_registry_host = arg
            found_d = True
        elif opt in ("-l", "--login-destination-registry"):
            destination_auth = arg
            found_l = True

        else:
            assert False, "Unhandled option provided"

    if not found_s or not found_m:
        print("You must specify the -s for Source Registry and -m for execution mode and -d for destination registry")
        print(found_s)
        print(found_m)
        print(found_d)
        sys.exit(2)

    print('Source Registry =', src_registry_host)
    print('Execution Mode =', mode)
    print('Desination Registry =', dst_registry_host)
    print('*****************************************')

    if found_l == True:
        docker_login(dst_registry_proto, dst_registry_host)

    # DOCKER REPO IMAGE MOVE from UNIVERSE IMAGE to DEST REGISTRY
    print("Querying Source Registry Host = " + src_registry_host)
    src_repos = get_registry_images(src_registry_proto, src_registry_host)
    src_manifests = get_registry_manifests(src_registry_proto, src_registry_host, src_repos)

    print("Querying Destination Registry Host = " + dst_registry_host)
    dst_repos = get_registry_images(dst_registry_proto, dst_registry_host)
    dst_manifests = get_registry_manifests(dst_registry_proto, dst_registry_host, dst_repos)
    # input("DEBUG PAUSE - Press Enter to continue . . . ")
    try:
        image_list = []
        for image, imagetag in src_manifests.items():
            print('Starting on Image : Tag (' + image + ':' + imagetag + ")")
            fullImageId = src_registry_host + "/" + image + ":" + imagetag
            print("Source Docker Image to Pull fullimageId = " + fullImageId)

            pull_images(fullImageId)
            new_image = tag_images(image, imagetag, fullImageId, dst_registry_host)
            print("Destination Docker Image to Push = " + new_image)
            push_images(new_image, docker_target)
            image_list.append(new_image)
    except (subprocess.CalledProcessError):
        print('MISSING Docker Images: {}'.format(image))

    print("\n \n New Images uploaded to " + dst_registry_host + " are " + str(image_list))
    input("DEBUG PAUSE - Press Enter to continue . . . ")
    # HTTP Artifacts
    # Copy out the entire nginx / html directory to data directory where script is being run.
    '''updated_universe_json_file = copy_http_data(working_directory, universe_json_file)
    '''


print("\n ********************* \n")
print("\n Program Finished \n")
