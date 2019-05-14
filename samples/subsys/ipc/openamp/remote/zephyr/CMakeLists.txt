if(FIRST_BOILERPLATE_EXECUTION)
  zephyr_add_executable(remote output_variable)

  if(output_variable)
    add_subdirectory(${CMAKE_CURRENT_LIST_DIR}/.. ${CMAKE_BINARY_DIR}/remote)
  endif()
endif()
